/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Wires the collaboration fixes and the Excel filter persistence into the
 * spreadsheet_oca renderer.
 */

import * as spreadsheet from "@odoo/o-spreadsheet";
import {onMounted, onWillUnmount, useExternalListener, useState, useSubEnv} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";
import {browser} from "@web/core/browser/browser";
import {patch} from "@web/core/utils/patch";
import {SpreadsheetRenderer} from "@spreadsheet_oca/spreadsheet/bundle/spreadsheet_renderer.esm";
import {CollabStatus} from "./collab_status.esm";
import {XL_FILTER_TOOL} from "./excel_filter/excel_keys.esm";
import {initLockState, installLockGuard, lockedBy, setLocks, typingTags} from "./cell_lock.esm";
import {upgradeTransport} from "./collab_transport.esm";
import {
    exportCriteria,
    filterStats,
    importCriteria,
    onCriteriaChange,
} from "./excel_filter/filter_state.esm";

const uuidGenerator = new spreadsheet.helpers.UuidGenerator();
const {CellComposerStore} = spreadsheet.stores;
const {toXC} = spreadsheet.helpers;
const MESSAGE_VERSION = 1;

SpreadsheetRenderer.components = {...SpreadsheetRenderer.components, CollabStatus};

patch(SpreadsheetRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        useSubEnv({xlFilterTool: XL_FILTER_TOOL});
        const info = this.props.record.collab;
        if (!info) {
            return; // Server side not upgraded: keep stock behaviour.
        }
        const model = this.spreadsheet_model;
        const session = model.session;
        this.collabInfo = info;
        this.collabState = useState({
            tick: 0,
            online: true,
            synced: true,
            readonly: this.props.record.mode === "readonly",
            liveUpdates: true,
            clients: [],
            hasVersions: info.has_versions,
            filter: {active: 0, total: 0, visible: 0},
            lock: null,
        });
        // Cell locking: first editor on a cell edits, others read until they leave.
        let lastBlockedToast = 0;
        initLockState(model, {
            // Readers see who is typing; they never claim a cell themselves.
            enabled: info.cell_lock,
            me: () => session.clientId,
            locks: info.locks,
            notifyBlocked: (holder) => {
                this._collabTriedCell = `${holder.sheetId}|${holder.col}|${holder.row}`;
                if (Date.now() - lastBlockedToast < 2500) {
                    return;
                }
                lastBlockedToast = Date.now();
                this.notifications.add(
                    _t(
                        "%(name)s is typing in %(cell)s. You can edit it when they finish.",
                        {cell: toXC(holder.col, holder.row), name: holder.name}
                    ),
                    {type: "warning"}
                );
            },
            onComposer: () => this.collabSyncTyping(),
        });
        installLockGuard(model);
        useSubEnv({collabTypingTags: () => typingTags(model.getters)});
        this.collabTransport = upgradeTransport(session.transportService, {
            model: this.props.model,
            resId: this.props.res_id,
            onReset: (payload) => this.collabOnReset(payload),
            onChange: () => this.collabRefresh(),
            onLocks: (locks) => this.collabOnLocks(locks),
        });
        model.on("update", this, () => this.collabRefresh());
        model.on("unexpected-revision-id", this, () => {
            // A notification was missed: fetch the gap instead of diverging.
            this.collabTransport.catchUp(session.getRevisionId());
        });

        useSubEnv({
            saveSpreadsheet: () => this.collabCheckpoint({kind: "checkpoint"}),
            collabSaveVersion: (name) =>
                this.collabCheckpoint({kind: "checkpoint", name}),
            collabOpenHistory: () => this.collabOpenHistory(),
            collabHasVersions: () => Boolean(info.has_versions),
        });

        // Ctrl+S saves a version (instead of the browser's "Save page").
        useExternalListener(
            window,
            "keydown",
            (ev) => {
                if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && ev.key.toLowerCase() === "s") {
                    ev.preventDefault();
                    ev.stopPropagation();
                    this.collabCheckpoint({kind: "checkpoint"});
                }
            },
            {capture: true}
        );
        // Tab closed: tell the others now, not when their presence times out.
        const onPageHide = () => this.collabBeacon();
        browser.addEventListener("pagehide", onPageHide);

        // Liveness: re-announce presence, drop clients gone silent.
        const timeout = (info.presence_timeout || 90) * 1000;
        const heartbeat = setInterval(() => this.collabHeartbeat(timeout), timeout / 3);
        const poll = setInterval(() => this.collabRefresh(), 700);
        // While the live channel is down, edits still go through RPC; others'
        // edits are pulled every few seconds until it comes back.
        let pullTimer = null;
        const offBusDisconnect = () => {
            this._collabBusOnline = false;
            clearInterval(pullTimer);
            pullTimer = setInterval(
                () => this.collabTransport.catchUp(session.getRevisionId()).catch(() => 0),
                5000
            );
            this.collabRefresh();
        };
        const onBusReconnect = () => {
            this._collabBusOnline = true;
            clearInterval(pullTimer);
            this.collabTransport.catchUp(session.getRevisionId()).catch(() => 0);
            this.collabHeartbeat(timeout);
        };
        this.bus_service.addEventListener("disconnect", offBusDisconnect);
        this.bus_service.addEventListener("reconnect", onBusReconnect);
        this.bus_service.addEventListener("connect", onBusReconnect);

        // Filters: reapply this user's saved criteria, save each change.
        let restoring = true;
        const offCriteria = onCriteriaChange(model, () => {
            this.collabRefresh();
            if (restoring) {
                return;
            }
            this.orm
                .call(this.props.model, "collab_save_user_filters", [
                    [this.props.res_id],
                    exportCriteria(model),
                ])
                .catch(() => undefined);
        });

        onMounted(() => {
            this._collabMounted = true;
            try {
                importCriteria(model, info.user_filters || []);
            } catch (error) {
                console.warn("Could not reapply saved filters", error);
            }
            restoring = false;
            if (info.snapshot_requested) {
                this.collabCheckpoint({kind: "snapshot", silent: true});
            }
            this.collabRefresh();
        });
        onWillUnmount(() => {
            clearInterval(heartbeat);
            clearInterval(poll);
            offCriteria();
            browser.removeEventListener("pagehide", onPageHide);
            this.bus_service.removeEventListener("disconnect", offBusDisconnect);
            this.bus_service.removeEventListener("reconnect", onBusReconnect);
            this.bus_service.removeEventListener("connect", onBusReconnect);
            clearInterval(pullTimer);
            this.bus_service.deleteChannel?.(session.transportService.channel);
            this.collabTransport.close();
        });
    },

    /* ------------------------------------------------------------------ */
    collabRefresh() {
        if (!this.collabState) {
            return;
        }
        const model = this.spreadsheet_model;
        const getters = model.getters;
        const s = this.collabState;
        s.synced = model.session.isFullySynchronized() && !this.collabTransport.isBusy();
        s.online = this.collabTransport.isOnline();
        s.liveUpdates = this._collabBusOnline !== false;
        let me = null;
        try {
            me = getters.getClient().id;
        } catch {
            me = null;
        }
        const colored = {};
        for (const c of getters.getClientsToDisplay()) {
            colored[c.id] = c.color;
        }
        const clients = [];
        const seenUsers = new Set();
        for (const client of getters.getConnectedClients()) {
            if (client.id === me || !client.name) {
                continue;
            }
            // One avatar per person even with several tabs open.
            const userKey = client.userId || client.id;
            if (seenUsers.has(userKey)) {
                continue;
            }
            seenUsers.add(userKey);
            const pos = client.position;
            clients.push({
                id: client.id,
                name: client.name,
                userId: client.userId,
                position: pos,
                color: colored[client.id],
                sheetName:
                    pos && pos.sheetId !== getters.getActiveSheetId()
                        ? getters.tryGetSheetName?.(pos.sheetId) || ""
                        : "",
            });
        }
        s.clients = clients;
        s.filter = filterStats(model, getters.getActiveSheetId());
        this.collabSyncTyping();
        const active = getters.getActivePosition();
        const holder = lockedBy(getters, active.sheetId, active.col, active.row);
        s.lock = holder ? {cell: toXC(holder.col, holder.row), name: holder.name} : null;
        s.tick++;
    },

    /**
     * Claim the cell when this person starts typing and release it when they
     * stop. The release waits until the typed value has reached the server, so
     * whoever types next starts from it. Calls run one after another; a call
     * made obsolete by a later change is skipped.
     */
    collabSyncTyping() {
        if (
            !this._collabMounted ||
            !this.collabTransport ||
            this.props.record.mode === "readonly"
        ) {
            return; // the store container only has the model once mounted
        }
        const composer = this.stores.get(CellComposerStore);
        const editing = composer.editionMode !== "inactive";
        const key = editing ? `${composer.sheetId}|${composer.col}|${composer.row}` : null;
        if (key === (this._typingKey ?? null)) {
            return;
        }
        this._typingKey = key;
        const session = this.spreadsheet_model.session;
        const position = editing
            ? {sheetId: composer.sheetId, col: composer.col, row: composer.row}
            : false;
        this._typingChain = (this._typingChain || Promise.resolve())
            .then(async () => {
                if (!position) {
                    await this.collabTransport.waitSynced(session, 5000);
                    if (this._typingKey !== null) {
                        return; // already typing elsewhere: that claim releases this one
                    }
                } else if (this._typingKey !== key) {
                    return;
                }
                const res = await this.orm.call(this.props.model, "collab_cell_typing", [
                    [this.props.res_id],
                    session.clientId,
                    position,
                ]);
                this.collabOnLocks(res.locks || []);
                if (position && !res.ok && this._typingKey === key) {
                    const editor = this.stores.get(CellComposerStore);
                    if (editor.editionMode !== "inactive") {
                        editor.cancelEdition();
                        this.stores.trigger("store-updated");
                    }
                    this.notifications.add(
                        _t("%(name)s started typing in %(cell)s first. It is read-only for you until they finish.", {
                            name: res.holder?.name || _t("Someone"),
                            cell: toXC(position.col, position.row),
                        }),
                        {type: "warning"}
                    );
                }
            })
            .catch(() => undefined);
    },

    collabOnLocks(locks) {
        const model = this.spreadsheet_model;
        const getters = model.getters;
        const active = getters.getActivePosition();
        const tried = this._collabTriedCell ? this._collabTriedCell.split("|") : null;
        const triedLock = () =>
            tried ? lockedBy(getters, tried[0], Number(tried[1]), Number(tried[2])) : null;
        const before = triedLock();
        setLocks(model, locks);
        const after = lockedBy(getters, active.sheetId, active.col, active.row);
        if (after && this._collabMounted) {
            // Arrived at the same moment as someone else and the server put
            // them first: close an editor opened in the meantime.
            const composer = this.stores.get(CellComposerStore);
            if (
                composer.editionMode !== "inactive" &&
                composer.col === after.col &&
                composer.row === after.row
            ) {
                composer.cancelEdition();
                this.stores.trigger("store-updated");
                this.notifications.add(
                    _t("%(name)s started typing in %(cell)s first. It is read-only for you until they finish.", {
                        name: after.name,
                        cell: toXC(after.col, after.row),
                    }),
                    {type: "warning"}
                );
            }
        }
        if (before && !triedLock()) {
            // Only for a cell this person actually tried to edit.
            this._collabTriedCell = null;
            this.notifications.add(
                _t("%(name)s finished typing in %(cell)s. You can edit it now.", {
                    name: before.name,
                    cell: toXC(before.col, before.row),
                }),
                {type: "success"}
            );
        }
        model.trigger("update");
        this.collabRefresh();
    },

    collabJump(client) {
        const model = this.spreadsheet_model;
        const pos = client.position;
        if (!pos) {
            return;
        }
        const active = model.getters.getActiveSheetId();
        if (pos.sheetId !== active) {
            model.dispatch("ACTIVATE_SHEET", {sheetIdFrom: active, sheetIdTo: pos.sheetId});
        }
        model.selection.selectCell(pos.col, pos.row);
    },

    collabHeartbeat(timeout) {
        const session = this.spreadsheet_model.session;
        const state = this.collabTransport.state;
        const now = Date.now();
        // Prune clients that stopped announcing themselves.
        for (const id of Object.keys(session.clients)) {
            if (id === session.clientId) {
                continue;
            }
            if (!state.lastSeen.has(id)) {
                state.lastSeen.set(id, now);
            } else if (now - state.lastSeen.get(id) > timeout) {
                delete session.clients[id];
                state.lastSeen.delete(id);
            }
        }
        const me = session.clients[session.clientId];
        if (me && me.position) {
            session.transportService.sendMessage({
                type: "CLIENT_MOVED",
                version: MESSAGE_VERSION,
                client: {...me},
            });
        }
        this.collabRefresh();
    },

    collabBeacon() {
        const session = this.spreadsheet_model.session;
        try {
            const blob = new Blob(
                [
                    JSON.stringify({
                        model: this.props.model,
                        res_id: this.props.res_id,
                        clientId: session.clientId,
                    }),
                ],
                {type: "text/plain"}
            );
            navigator.sendBeacon("/spreadsheet_oca_collab/leave", blob);
        } catch {
            // Best effort only.
        }
    },

    /**
     * Store the current state as a snapshot on the server, ordered like any
     * revision: it is refused if someone else's edit got in first.
     */
    async collabCheckpoint({kind = "checkpoint", name = false, silent = false} = {}) {
        const model = this.spreadsheet_model;
        if (!this.collabTransport || model.getters.isReadonly()) {
            return false;
        }
        const session = model.session;
        const synced = await this.collabTransport.waitSynced(session, 10000);
        if (!synced) {
            if (!silent) {
                this.notifications.add(
                    _t("Still sending your last edits. Try again in a moment."),
                    {type: "warning"}
                );
            }
            return false;
        }
        const snapshotId = uuidGenerator.uuidv4();
        const message = {
            type: "SNAPSHOT",
            version: MESSAGE_VERSION,
            serverRevisionId: session.getRevisionId(),
            nextRevisionId: snapshotId,
            clientId: session.clientId,
            data: {...model.exportData(), revisionId: snapshotId},
            collabKind: kind,
        };
        if (name) {
            message.versionName = name;
        }
        const accepted = await session.transportService.sendMessage(message);
        if (!silent) {
            if (accepted) {
                this.notifications.add(
                    name ? _t("Version “%s” saved.", name) : _t("Saved to version history."),
                    {type: "success"}
                );
            } else {
                this.notifications.add(
                    _t("Someone edited the sheet at the same moment. Press Ctrl+S again."),
                    {type: "info"}
                );
            }
        }
        return accepted;
    },

    async collabOpenHistory() {
        if (!this.collabInfo?.has_versions) {
            return;
        }
        const action = await this.orm.call(this.props.model, "action_open_versions", [
            [this.props.res_id],
        ]);
        await this.action.doAction(action);
    },

    collabOnReset(payload) {
        if (this._collabResetting) {
            return;
        }
        this._collabResetting = true;
        this.collabTransport.close();
        const text =
            payload.reason === "restore"
                ? _t("%s restored an earlier version. Reloading…", payload.by || _t("Someone"))
                : payload.reason === "desync"
                ? _t("This sheet changed while you were disconnected. Reloading…")
                : _t("The sheet was replaced. Reloading…");
        this.notifications.add(text, {type: "info", sticky: true});
        setTimeout(() => browser.location.reload(), 1200);
    },

    /** Leaving the sheet: never write a local copy over everyone else's. */
    async onSpreadsheetSaved() {
        if (!this.collabTransport) {
            return super.onSpreadsheetSaved(...arguments);
        }
        if (this._collabLeft) {
            return;
        }
        this._collabLeft = true;
        const model = this.spreadsheet_model;
        await this.collabTransport.waitSynced(model.session, 5000);
        try {
            // o-spreadsheet snapshots here when this was the last client; the
            // server accepts that snapshot only if it is at the current head.
            await model.leaveSession();
        } catch (error) {
            console.warn("leaveSession failed", error);
        }
        model.off("update", this);
        this.collabTransport.close();
    },
});
