import requests
import sys

from hashlib import md5

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 8.1; rv:10.0) Gecko/20100101 Firefox/10.0'
}

LJ_SERVER = ""
USERNAME = ""
PASSWORD = ""


def get_cookies():
    r1 = requests.post(LJ_SERVER + "/interface/flat", data={'mode': 'getchallenge'})
    r1_flat = flatresponse(r1.text)
    challenge = r1_flat['challenge']

    r2 = requests.post(LJ_SERVER + "/interface/flat",
                       data={'mode': 'sessiongenerate',
                             'user': USERNAME,
                             'auth_method': 'challenge',
                             'auth_challenge': challenge,
                             'auth_response': make_md5_from_challenge(challenge)
                             }
                       )

    r2_flat = flatresponse(r2.text)

    if r2_flat.get('ljsession', False):
        return {'ljsession': r2_flat['ljsession']}
    else:
        print("Did not get ljsession cookie.  Exiting")
        sys.exit(1)


def get_headers():
    return HEADERS


def flatresponse(response):
    items = response.strip('\n').split('\n')
    flat_response = {items[i]: items[i + 1] for i in range(0, len(items), 2)}
    return flat_response


def make_md5_from_challenge(challenge):
    first_encoded = challenge + md5(PASSWORD.encode('utf-8')).hexdigest()
    full_encoded = md5(first_encoded.encode('utf-8')).hexdigest()
    return full_encoded