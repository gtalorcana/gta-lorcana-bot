from constants import (
    SET_CHAMPS_SPREADSHEET_ID,
    SET_CHAMPS_EVENTS_RANGE_NAME,
    SET_CHAMPS_START_DT,
    SET_CHAMPS_END_DT,
)
from util.google_sheets_api_utils import GoogleSheetsApi
from util.rph_api_utils import RphApi

_gs = GoogleSheetsApi()
_rph_api = RphApi()

if __name__ == '__main__':
    override_params = {
        'display_status':     None,
        'display_statuses':   ['past', 'inProgress', 'upcoming'],
    }

    event_rows = _rph_api.get_events(start_date_after=SET_CHAMPS_START_DT, start_date_before=SET_CHAMPS_END_DT, extra_params=override_params)

    # _gs.update_values(SET_CHAMPS_SPREADSHEET_ID, SET_CHAMPS_EVENTS_RANGE_NAME, "USER_ENTERED", event_rows)

    '''
    https://api.cloudflare.ravensburgerplay.com/hydraproxy/api/v2/events/
    ?start_date_after=2026-04-04T04%3A00%3A00.000Z
    &start_date_before=2026-04-06T03%3A59%3A59.999Z
    &display_statuses=upcoming
    &display_statuses=inProgress
    &display_statuses=past
    &game_slug=disney-lorcana&latitude=43.7154&longitude=-79.3896&num_miles=25&page=1&page_size=25
    '''