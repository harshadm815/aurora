# Connecting AURORA to Gmail (read-only)

About 15 minutes, once, free. You create a small "project" in your own Google account that lets
AURORA ask for permission to read your mail. AURORA never sees your Google password: you sign in on
Google's own page, and you can revoke AURORA's access at any time.

## 1. Create the project (in your browser)

1. Go to **console.cloud.google.com** and sign in with the Gmail account AURORA should read.
   Accept Google Cloud's terms if it asks (that's your decision; it's free for this use).
2. Top bar → project picker → **New project** → name it `AURORA` → **Create**, then select it.

## 2. Switch on the Gmail API

3. Menu → **APIs & Services → Library** → search **Gmail API** → **Enable**.

## 3. The consent screen (what Google shows you when AURORA asks)

4. Menu → **Google Auth Platform** (or **APIs & Services → OAuth consent screen**) → **Get started**.
5. **App name** `AURORA`, **user support email**: your address → Next.
6. **Audience: External** → Next. **Contact email**: your address → Next → agree → **Create**.
7. **Audience** → **Test users** → **Add users** → your Gmail address → Save.
8. **Data access** → **Add or remove scopes** → tick
   `https://www.googleapis.com/auth/gmail.readonly` (read all your mail, no changes, no sending) →
   Update → Save.

## 4. The "Desktop app" client

9. **Clients** (or **Credentials**) → **Create client** → **Application type: Desktop app** → name
   `AURORA desktop` → **Create**.
10. **Download JSON**. Leave it in your Downloads folder and tell me; I'll move it to
    `I:\Aurora\Data\google_oauth_client.json` (the Data folder never goes into git). Don't paste its
    contents into the chat.

## 5. Connect

11. AURORA → **Settings → Gmail → Connect Gmail…** → your browser opens Google's sign-in → choose
    your account. While the project is in Testing, Google warns "Google hasn't verified this app":
    that's expected for your own project → **Continue** → allow **read your email** → "AURORA is
    connected to Gmail." Close the tab.
12. In Chat: "check my Gmail".

## End-of-day report

Settings → Gmail → tick **End-of-day email report at**, choose the time and where it's announced
(this PC, your phone, or both). For the phone, also tick **Email report** for your phone on the
Phone page. AURORA must be running at that time (the notification area is enough); if the PC was
asleep, the report is made when it's back that day. Ask "email report" in Chat to read it.

## Good to know

- **Every 7 days** Google ends the sign-in while the project is in Testing. AURORA then says so, and
  you press **Connect Gmail…** again. To stop that: Google Auth Platform → **Audience → Publish app**
  ("In production"). For your own personal use Google doesn't require its verification or security
  assessment, but sign-in keeps showing the "unverified app" warning.
- **Disconnect**: Settings → Gmail → Disconnect (asks Google to revoke access and deletes the sign-in
  from this PC), or at myaccount.google.com → Security → Third-party access.
- **Internet setting**: AURORA's internet access applies (Settings → Internet and network). In "ask"
  mode, the first Gmail check asks before AURORA contacts Google.
- **What's stored**: only Google's tokens, encrypted with Windows' per-user encryption
  (`Data\gmail_signin.bin`). Emails are read when you ask and aren't saved by AURORA.
- **Read-only**: AURORA can't send, delete or change anything in your mail with this permission.
  Sending replies will be a separate permission, asked for later, and every email will need your
  approval before it's sent.
