=======
History
=======

0.9.4 (2017-11-24)
------------------


0.9.3 (2017-11-22)
------------------
* don't spam on second try
* better logging config for different destinations

0.9.2 (2017-11-21)
------------------
* more error checking and responding with errors
* much much much polishing and refactoring

0.9.1 (2017-11-20)
------------------
* more error checking and responding
* limit youtube-dl time to download
* avoid live downloads
* logging refactor and refinement
* help updates

0.9.0 (2017-11-20)
------------------
* return of inline mode as fast download (link is sent to telegram servers for download)
* refactor and refinement
* help updates
* add some spam captions :)

0.8.3 (2017-11-19)
------------------
* gc.collect() according to https://github.com/jiaaro/pydub/issues/89#issuecomment-75245610

0.8.2 (2017-11-19)
------------------
* cool refinements in logging
* store urls, so button response is faster now

0.8.1 (2017-11-19)
------------------
* some logging fixes

0.8.0 (2017-11-19)
------------------
* many fixes and workarounds
* alerting & logging

0.7.10 (2017-11-05)
------------------
* botanio fix - send user id, not chat id

0.7.9 (2017-11-05)
------------------
* botanio fix
* tmpreaper config sample
* clutter fix

0.7.8 (2017-11-04)
------------------
* botanio
* maintenance

0.7.7 (2017-09-11)
------------------
* maintenance

0.7.6 (2017-09-11)
------------------
* SYSLOG_DEBUG env var to disable logging of full messages
* maintenance
* Logentries support

0.7.5.1 (2017-09-03)
------------------
* YouTube number remove

0.7.5 (2017-09-03)
------------------
* maintenance

0.7.4 (2017-08-03)
------------------
* msg_store fixes

0.7.3 (2017-07-20)
------------------
* orig_msg_id hotfix and don't send chat action on every link

0.7.2 (2017-07-19)
------------------
* Updated requirements

0.7.1 (2017-07-05)
------------------
* Hotfix

0.7.0 (2017-07-05)
------------------
* Travis CI, tests and docs from cookiecutter

0.6.3 (2017-07-04)
------------------

* Back to bandcamp-dl and scdl and download timeouts

0.6.2 (2017-07-04)
------------------

* Help message in groups now redirects to PM

0.6.1 (2017-07-03)
------------------

* Async run of download/send command
* Link command

0.6.0 (2017-07-02)
------------------

* Added text files to sdist
* Bandcamp and SoundCloud-widgets is now downloaded with youtube-dl
* Supported parsing widgets from pages
* Refactor

0.5.1 (2017-07-02)
------------------

* New clutter command
* Help refinements
* Some fixes

0.5.0 (2017-06-28)
------------------

* Big refactor to class-based
* Syslog support
* Some fixes

0.4.0 (2017-06-15)
------------------

* Console script!
* Setup script version improvements
* Ask in groups only, download immediately in private
* Bandcamp: Download links without 'bandcamp' for /dl
* Move TODOs to issues
* Button to destroy music from the Internet

0.3.1 (2017-06-12)
------------------

* Markdown to reStructuredText
* Copy tags to parts

0.3.0 (2017-06-10)
------------------

* YouTube playlists support
* Split audio by 50 MB size for sending
* Disable privacy mode and ask for download

0.2.0 (2017-06-06)
------------------

* Webhooks and async

0.1.0 (2017-06-04)
------------------

* First usable and stable version.
