/** @odoo-module **/
/* Copyright 2026 RLFB
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

import {ConnectionLostError} from "@web/core/network/rpc";

const ORDERED = new Set([
    "REMOTE_REVISION",
    "REVISION_UNDONE",
    "REVISION_REDONE",
    "SNAPSHOT",
]);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Upgrades the stock spreadsheet_oca transport in place.
 *
 * Stock behaviour: one RPC per message, no retry, nothing done when the server
 * refuses a revision. With server ordering in place a refusal is normal (some
 * else's revision got there first), so this adds:
 *  - retry with back-off while the connection is lost, so no edit is dropped;
 *  - catch-up after a refusal or reconnect: fetch the revisions the client is
 *    missing so o-spreadsheet can transform its pending work and resend;
 *  - interception of COLLAB_RESET (restore/replace) and presence liveness.
 *
 * @param {Object} transport SpreadsheetTransportService instance
 * @param {Object} opts {model, resId, onReset, onChange, onLocks}
 */
export function upgradeTransport(transport, opts) {
    const state = {
        online: true,
        closed: false,
        inFlight: 0,
        catchingUp: null,
        lastSeen: new Map(),
    };
    const deliver = transport._handleNotification.bind(transport);
    const changed = () => opts.onChange && opts.onChange();

    transport._handleNotification = (payload) => {
        if (state.closed) {
            return;
        }
        if (payload.type === "COLLAB_RESET") {
            opts.onReset(payload);
            return;
        }
        if (payload.type === "CELL_LOCKS") {
            opts.onLocks?.(payload.locks || []);
            return;
        }
        const now = Date.now();
        if (payload.client && payload.client.id) {
            state.lastSeen.set(payload.client.id, now);
        } else if (payload.clientId && payload.type !== "CLIENT_LEFT") {
            state.lastSeen.set(payload.clientId, now);
        }
        deliver(payload);
        changed();
    };

    async function call(method, args) {
        let delay = 500;
        for (;;) {
            if (state.closed) {
                throw new Error("transport closed");
            }
            try {
                const result = await transport.orm.call(opts.model, method, [
                    [opts.resId],
                    ...args,
                ]);
                if (!state.online) {
                    state.online = true;
                    changed();
                }
                return result;
            } catch (error) {
                if (!(error instanceof ConnectionLostError)) {
                    throw error;
                }
                if (state.online) {
                    state.online = false;
                    changed();
                }
                await sleep(delay);
                delay = Math.min(delay * 2, 10000);
            }
        }
    }

    /** @returns {Promise<number>} number of revisions delivered */
    async function catchUp(fromRevisionId) {
        if (state.catchingUp) {
            return state.catchingUp;
        }
        state.catchingUp = (async () => {
            try {
                const result = await call("get_spreadsheet_revisions_after", [
                    fromRevisionId,
                ]);
                if (result.reload) {
                    opts.onReset({reason: "desync"});
                    return 0;
                }
                for (const message of result.messages) {
                    transport._handleNotification(message);
                }
                return result.messages.length;
            } finally {
                state.catchingUp = null;
            }
        })();
        return state.catchingUp;
    }

    transport.sendMessage = async (message) => {
        if (state.closed) {
            return false;
        }
        if (!ORDERED.has(message.type)) {
            // Presence: best effort, never queued behind a lost connection.
            try {
                const ok = await transport.orm.call(
                    opts.model,
                    "send_spreadsheet_message",
                    [[opts.resId], message]
                );
                if (ok) {
                    if (ok.locks) {
                        opts.onLocks?.(ok.locks);
                    }
                    deliver(message);
                }
                return Boolean(ok);
            } catch {
                return false;
            }
        }
        state.inFlight++;
        changed();
        try {
            let accepted = await call("send_spreadsheet_message", [message]);
            if (accepted) {
                transport._handleNotification(message);
            } else {
                // Another revision was accepted first. Fetch it (and anything
                // else missed); the session transforms and resends.
                const delivered = await catchUp(message.serverRevisionId);
                if (delivered === 0 && !state.closed) {
                    // Nothing new on the server: the refusal was transient.
                    accepted = await call("send_spreadsheet_message", [message]);
                    if (accepted) {
                        transport._handleNotification(message);
                    }
                }
            }
            return accepted;
        } finally {
            state.inFlight--;
            changed();
        }
    };

    return {
        state,
        catchUp,
        isOnline: () => state.online,
        isBusy: () => state.inFlight > 0 || Boolean(state.catchingUp),
        async waitSynced(session, timeout = 10000) {
            const end = Date.now() + timeout;
            while (Date.now() < end) {
                if (session.isFullySynchronized() && !this.isBusy()) {
                    return true;
                }
                await sleep(100);
            }
            return false;
        },
        close() {
            state.closed = true;
        },
    };
}
