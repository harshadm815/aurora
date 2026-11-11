"""
The AURORA Browser: browsing inside the Command Centre (the Browser page).

The modules here don't need Qt, so they are tested on their own:

    addresses   what you type in the address bar -> an address or a search
    store       history and bookmarks (SQLite, on this PC only)
    reader      reader mode: a page's article text without menus and adverts
    answers     questions about the open page, answered by quoting it
    saving      "Save to Knowledge": the page's text as a Markdown document
    network     which hosts pages may contact (the network gate) and the list
                of sites each page contacted

The Qt WebEngine parts (tabs, profile, request interceptor, downloads) are
in aurora/interface/browser_view.py. See Docs/BROWSER_AUDIO_VIDEO.md.
"""
