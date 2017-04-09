# livejournal-export

Forked from arty-name/livejournal-export, and heavily modified as a hobby project.

Here's what's downloaded:

- Your posts
- Your LJ friends comments to your posts
- Your LJ friends userpic

These are all placed in a folder named "exported_journals".
From this you can create a static website of your LiveJournal entries using Pelican, a static website generator.

## To run

Requires Python 3.6+ (hasn't been tested with anything else), and hasn't been tested on Windoww.

Create the Python virtualenvironment with this command:

    python3 -m venv venv; . venv/bin/activate; pip install -r requirements.txt

## ljconfig.py

Edit this file with your LiveJournal username and password.
Also enter the dates you wish to download.

## export.py

This script will do the exporting. Run it after you 
have provided cookies and years as described below.
You will end up with full blog contents in several 
formats. `posts-html` folder will contain basic HTML
of posts and comments. `posts-markdown` will contain
posts in Markdown format with HTML comments and metadata 
necessary to [generate a static blog with Pelican](http://docs.getpelican.com/).
`posts-json` will contain posts with nested comments 
in JSON format should you want to process them further.

