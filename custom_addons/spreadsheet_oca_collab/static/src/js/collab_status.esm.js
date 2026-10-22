/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
 *
 * Header strip: who is in the sheet, save state, filter mode.
 */

import {Component, onWillUnmount, useState} from "@odoo/owl";
import {_t} from "@web/core/l10n/translation";

const NEUTRAL = "#8a8f98";
const MAX_AVATARS = 5;

export class CollabStatus extends Component {
    static template = "spreadsheet_oca_collab.CollabStatus";
    static props = {collab: Object, onJump: Function, onHistory: Function};

    setup() {
        this.colors = {};
        this.ui = useState({open: false});
        const close = () => (this.ui.open = false);
        window.addEventListener("click", close);
        onWillUnmount(() => window.removeEventListener("click", close));
    }

    get clients() {
        const c = this.props.collab;
        // Reading tick makes the strip re-render on every model or session change.
        void c.tick;
        return c.clients.map((client) => {
            const color = client.color || this.colors[client.id] || NEUTRAL;
            if (client.color) {
                this.colors[client.id] = client.color;
            }
            return {...client, color};
        });
    }
    get shown() {
        return this.clients.slice(0, MAX_AVATARS);
    }
    get overflow() {
        return this.clients.slice(MAX_AVATARS);
    }
    get status() {
        const c = this.props.collab;
        if (c.readonly) {
            return {cls: "readonly", icon: "fa-eye", text: _t("View only")};
        }
        if (!c.online) {
            return {
                cls: "offline",
                icon: "fa-chain-broken",
                text: _t("Offline: edits will sync when the connection returns"),
            };
        }
        if (!c.synced) {
            return {cls: "saving", icon: "fa-circle-o-notch fa-spin", text: _t("Saving…")};
        }
        if (!c.liveUpdates) {
            return {
                cls: "saving",
                icon: "fa-refresh",
                text: _t("Saved · reconnecting live updates"),
            };
        }
        return {cls: "saved", icon: "fa-check", text: _t("All changes saved")};
    }
    initials(name) {
        return (name || "?")
            .split(/\s+/)
            .slice(0, 2)
            .map((p) => p[0])
            .join("")
            .toUpperCase();
    }
    avatarUrl(client) {
        return client.userId ? `/web/image/res.users/${client.userId}/avatar_128` : "";
    }
    title(client) {
        return client.sheetName ? `${client.name} · ${client.sheetName}` : client.name;
    }
    toggleList(ev) {
        ev.stopPropagation();
        this.ui.open = !this.ui.open;
    }
}
