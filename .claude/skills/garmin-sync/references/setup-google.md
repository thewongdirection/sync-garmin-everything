# Setting up Google Health API access (the user does this)

**Check feasibility first.** As of 2026-10-12 Google states it is "not onboarding new projects at
this time" (https://developers.google.com/health). If enabling the API fails or the OAuth client
cannot be used, the Google sink is unavailable; the archive and Health Connect export still work.

Steps (https://developers.google.com/health/setup):

1. In [Google Cloud Console](https://console.cloud.google.com/), create or pick a project and enable
   the **Google Health API**.
2. *Credentials → Create credentials → OAuth client ID* → type **Web application**; under
   *Authorized redirect URIs* add exactly `https://www.google.com`. Download the JSON.
3. *OAuth consent screen* (Audience): user type **External**; add your own Google account under
   **Test users**. Leave it in **Testing** while setting up.
4. In your terminal (not in chat), validate and store the client file:
   `garmin-sync auth google init --client-file ~/Downloads/client_secret_XXXX.json`
   then delete the download; a private copy is kept at `~/.garmin-sync/google_client.json`.
5. Still in your terminal: `garmin-sync auth google login`. Open the printed URL, approve **every**
   requested permission, and copy the **full address** of the page you land on (it begins
   `https://www.google.com/?code=...&state=...`). Paste it at the hidden prompt.
6. Verify: `garmin-sync auth google check`.

## For the unattended routine

In *Testing* mode Google expires refresh tokens after **7 days**, which would break a routine every
week. Before setting the routine up, move the app to **In production** (consent screen → *Publish
app*). It stays *unverified* (users see a warning screen, capped at 100 users), which is fine for your
own account. Then run `auth google login` again so the refresh token you export is a production one.
Whether restricted scopes behave differently for a single owner in this state is not confirmed in
Google's documentation; the routine spike checks that the token still works after more than 7 days.

## Notes

* All eight read+write scopes are requested because with write-only scopes Google only lets the app see
  data it wrote itself, which would make duplicate detection impossible.
* To revoke: https://myaccount.google.com/permissions
