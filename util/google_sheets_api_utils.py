import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class GoogleSheetsApi:
    def __init__(self):
        self.creds = None

        _dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _token_path = os.path.join(_dir, "var", "token.json")
        _creds_path = os.path.join(_dir, "var", "credentials.json")

        token_env  = os.getenv("GOOGLE_TOKEN_JSON")
        creds_env  = os.getenv("GOOGLE_CREDENTIALS_JSON")
        use_env    = bool(token_env and creds_env)

        # ── Step 1: Load credentials ───────────────────────────────────────────
        #
        # Two paths depending on environment:
        #
        #   Fly.io (production):
        #     Both GOOGLE_TOKEN_JSON and GOOGLE_CREDENTIALS_JSON are set as
        #     Fly.io secrets (via `flyctl secrets set`). Credentials are loaded
        #     directly from those env vars — no files on disk are needed or used.
        #
        #   Local dev:
        #     Env vars are not set. Credentials are loaded from var/token.json,
        #     which is gitignored and lives only on your local machine.

        if use_env:
            self.creds = Credentials.from_authorized_user_info(
                json.loads(token_env), SCOPES
            )
        elif os.path.exists(_token_path):
            self.creds = Credentials.from_authorized_user_file(_token_path, SCOPES)

        # ── Step 2: Refresh if expired ─────────────────────────────────────────
        #
        # Google access tokens expire after ~1 hour. The refresh token is used to
        # obtain a new access token automatically without user interaction.
        #
        # On Fly.io: the refreshed token is written back to os.environ["GOOGLE_TOKEN_JSON"]
        #   so it stays valid for the lifetime of the current process. This is
        #   in-memory only — it does NOT update the Fly.io secret permanently.
        #   On the next machine restart, the original secret is loaded again and
        #   refreshed again. This is fine as long as the refresh token itself is valid.
        #
        # On local dev: the refreshed token is saved back to var/token.json as usual.
        #
        # ── If auth ever breaks (token fully expired or revoked) ───────────────
        #
        #   Google refresh tokens expire if:
        #     - Unused for 6+ months
        #     - The Google Cloud project has been reconfigured or consent revoked
        #     - The token was manually revoked in your Google account settings
        #
        #   Symptoms: bot logs show a 401 or "invalid_grant" error from Google.
        #
        #   Fix:
        #     1. On your local machine, delete var/token.json
        #     2. Run the bot locally once (python bot.py or python discord_sync_commands.py)
        #        — a browser window will open asking you to log in with Google
        #     3. Complete the OAuth flow — var/token.json will be regenerated
        #     4. Update the Fly.io secret with the new token:
        #          flyctl secrets set GOOGLE_TOKEN_JSON="$(cat var/token.json)" --app gta-lorcana-bot
        #     5. Fly.io will automatically redeploy with the new secret

        if not self.creds or not self.creds.valid:
            if self.creds and self.creds.expired and self.creds.refresh_token:
                self.creds.refresh(Request())
                if use_env:
                    # Update in-process env var so subsequent calls in this
                    # session use the refreshed access token.
                    os.environ["GOOGLE_TOKEN_JSON"] = self.creds.to_json()
                else:
                    with open(_token_path, "w") as token:
                        token.write(self.creds.to_json())
            else:
                # Credentials are missing or the refresh token itself is invalid.
                # Interactive OAuth (browser flow) is required — this can only be
                # done locally. On Fly.io we raise immediately with clear instructions.
                if use_env:
                    raise RuntimeError(
                        "Google credentials are invalid and cannot be refreshed automatically.\n"
                        "The refresh token may have expired or been revoked.\n\n"
                        "To fix:\n"
                        "  1. On your local machine, delete var/token.json\n"
                        "  2. Run the bot locally — a browser window will open for Google login\n"
                        "  3. Complete the OAuth flow to regenerate var/token.json\n"
                        "  4. Update the Fly.io secret:\n"
                        "       flyctl secrets set GOOGLE_TOKEN_JSON=\"$(cat var/token.json)\" --app gta-lorcana-bot\n"
                        "  5. Fly.io will redeploy automatically with the new secret"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(_creds_path, SCOPES)
                self.creds = flow.run_local_server(port=0)
                with open(_token_path, "w") as token:
                    token.write(self.creds.to_json())

        try:
            self.service = build("sheets", "v4", credentials=self.creds)
            self.sheet = self.service.spreadsheets()
        except HttpError as err:
            raise

    def get_values(self, spreadsheet_id, range_name):
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .get(spreadsheetId=spreadsheet_id, range=range_name)
                .execute()
            )
            rows = result.get("values", [])
            print(f"  {len(rows)} rows retrieved")
            return result
        except HttpError as error:
            raise

    def clear_values(self, spreadsheet_id, range_name):
        try:
            result = (
                self.service.spreadsheets()
                .values()
                .clear(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                )
                .execute()
            )
            return result
        except HttpError as error:
            raise

    def update_values(self, spreadsheet_id, range_name, value_input_option, _values):
        try:
            body = {"values": _values}
            result = (
                self.service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption=value_input_option,
                    body=body,
                )
                .execute()
            )
            print(f"  {result.get('updatedCells')} cells updated")
            return result
        except HttpError as error:
            raise

    def batch_update_values(self, spreadsheet_id, value_ranges: list):
        """
        Update multiple ranges in a single API call.
        value_ranges: list of {"range": "Sheet!A1:J1", "values": [[...]]}
        """
        try:
            body = {
                "valueInputOption": "USER_ENTERED",
                "data": value_ranges,
            }
            result = (
                self.service.spreadsheets()
                .values()
                .batchUpdate(spreadsheetId=spreadsheet_id, body=body)
                .execute()
            )
            print(f"  {result.get('totalUpdatedCells')} cells batch-updated")
            return result
        except HttpError as error:
            raise

    def append_values(self, spreadsheet_id, range_name, value_input_option, _values):
        try:
            body = {"values": _values}
            result = (
                self.service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=spreadsheet_id,
                    range=range_name,
                    valueInputOption=value_input_option,
                    body=body,
                )
                .execute()
            )
            print(f"  {result.get('updates').get('updatedCells')} cells appended")
            return result
        except HttpError as error:
            raise