# Helpdesk notifications: desktop pop-up and email

How helpdesk ticket notifications work on our Odoo, what the add-on
`helpdesk_notify_both` changes, and how to set it up and test it.

---

## 1. What we wanted

1. When a ticket is **created**, the helpdesk team hears about it.
2. When a ticket **changes stage** (New → In Progress → Done, Cancelled …),
   the team hears about it too.
3. Each notification arrives **both** ways:
   - as a **desktop pop-up** (and in the Odoo inbox), even when you are in
     another program;
   - as an **email**.

---

## 2. What Odoo does on its own (before the add-on)

### Each user picks one channel, never both

Every internal user has a setting in **My Profile → Preferences →
Notification**:

| Setting | Where notifications go |
|---|---|
| **Handle by Emails** | Email only. No inbox entry, no desktop pop-up. |
| **Handle in Odoo** | Odoo inbox (💬 icon) and desktop pop-up. **No email.** |

Odoo stores exactly one notification per message per person, so it can only
deliver it one way.

### Ticket created: already notified

`helpdesk_mgmt` (the OCA helpdesk) already has a notification type, called a
**subtype**, named **"Helpdesk Ticket Created"** on teams. Whoever follows a
team with it ticked is told about every new ticket in that team.

### Stage changed: nobody was notified

`helpdesk_mgmt` writes a stage change into the ticket's history as an
**internal note**. Notes don't notify anyone, so a ticket could go to Done
and nobody would hear about it. (The stage's own email template, if one is
set on the stage, emails the *customer*; that is separate and unchanged.)

### How the desktop pop-up works

Odoo 18 has web push built in. When a notification is for a user on
**Handle in Odoo**, Odoo sends it to every browser that user has allowed
notifications in. The pop-up appears even if the Odoo tab is closed, as long
as the browser itself is running. Clicking it opens the ticket.

---

## 3. What the add-on changes

Add-on: `custom_addons/helpdesk_notify_both/` (depends on `helpdesk_mgmt`).

### Change 1: stage changes now notify followers

- New subtype **"Stage Changed"** on tickets, ticked by default.
- New subtype **"Ticket Stage Changed"** on teams, also ticked by default.
  It is linked to the ticket one, so **following a team is enough**: you
  are automatically subscribed to stage changes on every new ticket in
  that team.
- A stage change is now posted as "Stage Changed" instead of an internal
  note, so everyone following with that subtype is notified.
- **Internal only.** Customers do not get these; they keep getting the
  stage email template exactly as before.
- When the add-on is installed, internal users who **already** follow a
  team or a ticket are given the new subtype automatically (otherwise only
  people who follow from now on would get it).

### Change 2: "Handle in Odoo" users also get the email

On helpdesk tickets only, an internal user set to **Handle in Odoo** now
gets:

- the Odoo inbox entry ✔ (as before)
- the desktop pop-up ✔ (as before)
- **plus** the same notification by email ✔ (new)

Users on **Handle by Emails** are unchanged: email only, no pop-up. So the
setting to choose for "both" is **Handle in Odoo**.

This applies to every ticket notification such a user receives: ticket
created, stage changed, assigned to you, and messages on the ticket.

### How the email part works (technical)

Odoo's own email step (`_notify_thread_by_email`) only emails people set to
"Handle by Emails". The add-on extends it on `helpdesk.ticket`. After Odoo
has done its normal work, the add-on takes the recipients who are internal
users on "Handle in Odoo" and have an email address, and builds the same
email Odoo would have built for them (same layout, same "View Ticket"
button).

It sends that email **without** creating a second notification record,
because Odoo allows only one per person per message and the inbox one
already exists. One side effect: that email's delivery status is not shown
on the message in the chatter. It is listed in **Settings → Technical →
Emails** like any other email.

### Files

| File | What it does |
|---|---|
| `__manifest__.py` | Name, version, depends on `helpdesk_mgmt`, loads the data file, runs the install hook. |
| `data/mail_message_subtype_data.xml` | The two new subtypes: "Stage Changed" (ticket) and "Ticket Stage Changed" (team, linked by `team_id`). |
| `models/helpdesk_ticket.py` | `_track_subtype`: posts stage changes with the new subtype. `_notify_thread_by_email` and `_notify_inbox_users_by_email`: the extra email for "Handle in Odoo" users. |
| `hooks.py` | On install, adds the new subtypes to internal users who already follow teams or tickets. |

---

## 4. Installing it

1. Push the repo. Railway rebuilds and restarts Odoo.
2. In Odoo: **Apps → Update Apps List**, remove the "Apps" filter, search
   **"Helpdesk Notifications"**, click **Install**.

Later updates are picked up on each deploy automatically (the Dockerfile
starts Odoo with `-u base`, which upgrades every installed module).

---

## 5. Setting it up for each person

Each person who should be notified does this once, on their own login.

1. **Email address on the user.** Settings → Users → open the user → Email
   Address. Without one the user cannot even create a ticket ("Unable to
   send message, please configure the sender's email address").
2. **Notification: Handle in Odoo.** Avatar (top right) → **My Profile** →
   **Preferences** → Notification → **Handle in Odoo** → save.
3. **Follow the team.** Helpdesk → Configuration → Teams → open the team →
   **Follow**. Then click the person icon next to *Following* → pencil next
   to your name → check that **Helpdesk Ticket Created** and **Ticket Stage
   Changed** are both ticked → Apply.
4. **Allow notifications in the browser.** Click the 💬 icon in Odoo's top
   bar; if it shows "Turn on notifications", click it and then **Allow**.
   Or: icon left of the address → Site settings → Notifications → Allow.
5. **Browser-specific:**
   - **Chrome:** Settings → System → turn on *Continue running background
     apps when Google Chrome is closed*.
   - **Brave:** `brave://settings/privacy` → turn on *Use Google services
     for push messaging* → restart Brave. Without this, Odoo shows
     "Failed to enable push notifications: push service error".
6. **Operating system:** notifications allowed for the browser, Do Not
   Disturb / Focus off.
7. Optional: avatar menu → **Install App**, for a separate Odoo window
   with its own taskbar icon.

---

## 6. Testing it

Use two users: **A** creates and changes tickets, **B** is notified. Odoo
never notifies you about your own actions, so testing alone with one user
shows nothing.

1. B: steps 1–6 of section 5, then switch to another program.
2. A (private window): create a ticket in B's team.
   → B gets a desktop pop-up **"Ticket created"**, and an email.
3. A: move the ticket to **In Progress**.
   → B gets a pop-up and an email showing the stage change.
4. A: move it to **Done**.
   → same again.
5. Check the emails were created: Settings → Technical → Emails (needs
   developer mode).

---

## 7. Things to know

- **Email needs an outgoing mail server.** Production has none yet. Until
  it does, the pop-ups work and the emails are created but fail to send
  (visible in Settings → Technical → Emails, status "Delivery Failed").
  Once a mail server is set up they go out normally.
- **Pop-ups need the browser running** and notifications allowed on each
  device and browser separately.
- **Only helpdesk tickets** get the "both" behaviour. Other apps (CRM,
  Sales …) still follow the user's single setting.
- **Customers and portal users** are not affected by either change.
- **Turning it off:** uninstall "Helpdesk Notifications: Odoo and Email" in
  Apps. Stage changes go back to silent notes and "Handle in Odoo" users
  stop getting the email copy.
- **Not tested on a real database yet.** The code compiles and was checked
  against Odoo 18's mail code, but install it on a copy of the database or
  test it right after installing.
