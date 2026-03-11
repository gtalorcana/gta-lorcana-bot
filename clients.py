"""
Shared API client singletons.

GoogleSheetsApi and RphApi are constructed once here and imported by any
module that needs them. This is critical for memory:

googleapiclient.discovery.build() — called inside GoogleSheetsApi.__init__ —
downloads and parses Google's full API discovery document, allocating ~160 MB
per instance. Constructing multiple instances (e.g. one in results.py and one
in stores.py) would double this cost at startup and leave no headroom on the
Fly.io shared-cpu-1x (512 MB) machine.

By constructing once here, the discovery document is loaded exactly once per
process lifetime regardless of how many modules import these clients.
"""

from util.google_sheets_api_utils import GoogleSheetsApi
from util.rph_api_utils import RphApi

gs      = GoogleSheetsApi()
rph_api = RphApi()