import requests
import os
from pathlib import Path
import time
from lxml import etree
import json

BASE_URL = ".livejournal.com/data/foaf.rdf"

DEFAULT_USERPIC_FILE = 'lj-default-userpic.png'
FOAF_BASE_URL = ".livejournal.com/data/foaf.rdf"

MIME_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

USERPIC_WORKING_DIR = "userpics"
USERPIC_METADATA_FILE = Path(USERPIC_WORKING_DIR, "userpics_metadata.json")

INITIAL_USERPIC_METADATA = {
    'anonymous': {
        'username': 'anonymous',
        'filename': 'lj-default-userpic.png',
        'state': 'local'
    }
}


# For testing
def main():
    lj_user = ''
    get_friends_default_pics_for_user(lj_user)


def get_friends_default_pics_for_user(username):
    rv = {
        "status": "ok"
    }

    userpix_dir = userpic_dirs['pix']
    rdf_dir = userpic_dirs['rdfs']

    # Get the user FOAF rdf file
    user_rdf_file = ensure_rdf_for_user(username, rdf_dir)
    if not user_rdf_file:
        rv = {
            "status": "error",
            "reason": "rdf file not found"
        }
        return rv

    # Get a dictionary of URLs to download from the rdf file
    pix_urls = get_userpic_urls_from_rdf(user_rdf_file)

    for username, pic_url in pix_urls.items():
        _ = get_userpic(username, userpix_dir, url=pic_url)

    return rv


def get_userpic_urls_from_rdf(rdf_file, username=None):
    rv = {}

    with open(rdf_file, 'rb') as f:
        root = etree.XML(f.read())

    # If username, we're searching for a particular user.  If not, retrieve all users in the FOAF RDF file
    if username:
        records = root.xpath(f"//foaf:Person[foaf:nick='{username}']",
                             namespaces={"foaf": "http://xmlns.com/foaf/0.1/"})
    else:
        records = root.xpath('//foaf:Person', namespaces={"foaf": "http://xmlns.com/foaf/0.1/"})

    for r in records:
        nick, image_url = None, None

        nick_elem = r.find('{http://xmlns.com/foaf/0.1/}nick')
        if nick_elem is not None and len(nick_elem.text):
            nick = nick_elem.text
        else:
            continue

        image_elem = r.find('{http://xmlns.com/foaf/0.1/}image')

        # For friends of the user
        if image_elem is not None:
            image_url = image_elem.text
        else:
            # For the actual user of the RDF file, the image URL is kept in the attributes of the img element
            img_elem = r.find('{http://xmlns.com/foaf/0.1/}img')
            if img_elem is not None and not img_elem.text:
                img_dict = img_elem.attrib
                for k, v in img_dict.items():
                    if k == '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}resource' and \
                            str(v).startswith('http://l-userpic'):
                        image_url = v
                        break

        if nick and image_url:
            rv[nick] = image_url

        # Update metadata as needed -- firstly, for ljusers with no imag
        if not userpics_meta.get(nick, False):
            update_metadata({
                'username': nick,
                'image_url': image_url
            })

        # Next for ljusers that haven't had an image_url set
        if not userpics_meta[nick].get('image_url', False):
            update_metadata({
                'username': nick,
                'image_url': image_url
            })

    return rv


# --------------------------------------------------------------------------------------------------------------------
def ensure_rdf_for_user(username, rdf_dir):
    rv = {
        "username": username,
        "rdf_file": username + ".rdf"
    }

    user_rdf_file = Path(rdf_dir, username + ".rdf")

    if not user_rdf_file.is_file():
        user_rdf_file = download_rdf(username, rdf_dir)
        if user_rdf_file:
            time.sleep(1)  # play nice and sleep for two seconds
        else:
            rv['rdf_file'] = "missing"

    update_metadata(rv)

    return user_rdf_file


def download_rdf(username, download_dir):
    save_file = Path(download_dir, username + ".rdf")

    r = requests.get(f'http://{username}{BASE_URL}')
    if r.status_code == requests.codes.ok:
        with open(save_file, 'wb') as f:
            f.write(r.content)

        return save_file

    else:
        return None


def get_userpic(username, pix_dir=None, url=None, download=True, force_download=False):
    rv = {
        'username': username,
        'status': None,
        'filename': None,
        'state': None
    }

    if not pix_dir:
        pix_dir = userpic_dirs['pix']

    found_file = False

    # Check in the metadata file first and return early if not forcing a download
    if userpics_meta.get(username, False) and not force_download:
        userdata = userpics_meta[username]

        # if downloaded, state is local.  If state is absent, needs to be downloaded again.
        userpic_state = userdata.get('state', False)
        if userpic_state and userpic_state == 'local':
            rv['status'] = 'ok'
            rv['filename'] = userdata.get('filename', DEFAULT_USERPIC_FILE)
            rv['state'] = userdata['state']
            return rv

    if not force_download:
        for _, ext in MIME_EXTENSIONS.items():
            test_file = Path(pix_dir, username + ext)
            if test_file.is_file():
                found_file = True
                rv['status'] = 'ok'
                rv['filename'] = test_file.name
                rv['state'] = 'local'
                break

    if (not found_file and download) or force_download:
        if url is None:
            # go get the URL
            # userpix_dir = userpic_dirs['pix']
            rdf_dir = userpic_dirs['rdfs']

            # Get the user FOAF rdf file
            user_rdf_file = ensure_rdf_for_user(username, rdf_dir)
            if not user_rdf_file:
                rv = {**rv, **{
                    "status": "error",
                    "state": "local",
                    "reason": "rdf file not found",
                    "filename": DEFAULT_USERPIC_FILE
                }
                      }
                update_metadata(rv)
                return rv

            # Get a dictionary of URLs to download from the rdf file
            pix_urls = get_userpic_urls_from_rdf(user_rdf_file, username)

            for username, pic_url in pix_urls.items():
                rv = download_userpic(username, pic_url, pix_dir)

                # Update metadata file for this user
                update_metadata(rv)

            # this returns the last value, which isn't quite right but is ok
            return rv

        else:
            rv = download_userpic(username, url, pix_dir)

    update_metadata(rv)

    return rv


def download_userpic(username, url, download_dir):
    rv = {
        'username': username,
        'status': None,
        'filename': None,
        'state': None
    }

    r = requests.get(url)
    if r.status_code == requests.codes.ok:
        content_type = r.headers['content-type']
        download_file = Path(download_dir, username + MIME_EXTENSIONS[content_type])
        with open(download_file, 'wb') as f:
            f.write(r.content)

        time.sleep(1)  # avoids throttling

        rv['status'] = 'ok'
        rv['filename'] = str(download_file)
        rv['state'] = 'downloaded'

    else:
        rv['status'] = 'error'
        rv['filename'] = DEFAULT_USERPIC_FILE
        rv['state'] = 'not downloaded'

    return rv


# ------------------------------------------------------------------------------------------
# Some metadata for users, so users without FOAF RDF files are marked as 'missing' and not downloaded.
# These are typically deleted users
#
# Note that since these are module-global items, they need to live in this file in a specific order

def create_metadata(filepath=USERPIC_METADATA_FILE, initial_data=INITIAL_USERPIC_METADATA):
    with open(filepath, 'w') as f:
        f.write(json.dumps(initial_data, ensure_ascii=False, indent=2))

    with open(filepath, 'r') as f:
        json_loaded = json.load(f)

    return json_loaded


def read_metadata(filepath=USERPIC_METADATA_FILE):
    if not filepath.exists():
        create_metadata(filepath=filepath)

    try:
        with open(filepath) as f:
            json_loaded = json.load(f)

    except json.JSONDecodeError:
        json_loaded = create_metadata(filepath=filepath)

    return json_loaded


def update_metadata(userdata, metadata_file=USERPIC_METADATA_FILE):
    user_to_update = userdata['username']
    existing_userdata = userpics_meta.get(user_to_update, {})

    merged_data = {**existing_userdata, **userdata}
    userpics_meta[user_to_update] = merged_data

    with open(metadata_file, 'w') as f:
        f.write(json.dumps(userpics_meta, ensure_ascii=False, indent=2))

    return userpics_meta


def ensure_userpic_dirs(top_dir):
    export_dirs = {
        "rdfs": os.path.join(top_dir, 'rdfs'),
        "pix": os.path.join(top_dir, 'pix'),
    }

    for k, v in export_dirs.items():
        os.makedirs(v, exist_ok=True)

    return export_dirs


# -----
userpic_dirs = ensure_userpic_dirs(USERPIC_WORKING_DIR)
userpics_meta = read_metadata()

if __name__ == '__main__':
    main()
