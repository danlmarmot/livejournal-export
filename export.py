#!/usr/bin/python3

import sys
import json
import os
import re
import html2text
import markdown
import fnmatch
from bs4 import BeautifulSoup
from datetime import datetime
from operator import itemgetter
import xml.etree.ElementTree as ET
from hashlib import md5
import requests

# User settings are found in ljconfig.py
import ljconfig as config

# Other constants
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 8.1; rv:10.0) Gecko/20100101 Firefox/10.0'
}

DOWNLOADED_JOURNALS_DIR = "exported_journals"

TAG = re.compile(r'\[!\[(.*?)\]\(http:\/\/utx.ambience.ru\/img\/.*?\)\]\(.*?\)')
USER = re.compile(r'<lj user="?(.*?)"?>')
TAGLESS_NEWLINES = re.compile('(?<!>)\n')
NEWLINES = re.compile('(\s*\n){3,}')
SLUGS = {}


def main():
    # Setup export directories for this LJ user
    export_dirs = ensure_export_dirs(DOWNLOADED_JOURNALS_DIR, config.username)

    if False:
        download_posts(export_dirs['posts-xml'])
        download_comments(export_dirs['comments-xml'], export_dirs['comments-json'])

    # Generate the all.json files from downloaded posts and comments
    if False:
        create_posts_json_all_file(export_dirs['posts-xml'], export_dirs['posts-json'])
        create_comments_json_all_file(export_dirs['comments-xml'], export_dirs['comments-json'])

    if True:
        with open(os.path.join(export_dirs['posts-json'], 'all.json'), 'r') as f:
            all_posts = json.load(f)
        with open(os.path.join(export_dirs['comments-json'], 'all.json'), 'r') as f:
            all_comments = json.load(f)

        combine(all_posts, all_comments, export_dirs)


def ensure_export_dirs(top_dir, lj_user):
    export_dirs = {
        "posts-xml": os.path.join(top_dir, lj_user, 'posts-xml'),
        "posts-json": os.path.join(top_dir, lj_user, 'posts-json'),
        "posts-html": os.path.join(top_dir, lj_user, 'posts-html'),
        "posts-markdown": os.path.join(top_dir, lj_user, 'posts-markdown'),
        "comments-xml": os.path.join(top_dir, lj_user, 'comments-xml'),
        "comments-json": os.path.join(top_dir, lj_user, 'comments-json'),
        "comments-html": os.path.join(top_dir, lj_user, 'comments-html'),
        "comments-markdown": os.path.join(top_dir, lj_user, 'comments-markdown'),
    }

    for k, v in export_dirs.items():
        os.makedirs(v, exist_ok=True)

    return export_dirs


def find_files_by_pattern(filepat, top_dir):
    for path, dirlist, filelist in os.walk(top_dir):
        for name in fnmatch.filter(filelist, filepat):
            yield os.path.join(path, name)


def create_posts_json_all_file(posts_xml_dir, posts_json_dir):
    xml_posts = []

    xml_files = find_files_by_pattern('*.xml', posts_xml_dir)
    for xml_file in xml_files:
        with open(xml_file, 'rt') as f:
            xml_posts.extend(list(ET.fromstring(f.read()).iter('entry')))

    json_posts = list(map(xml_to_json, xml_posts))
    posts_json_all_filename = os.path.join(posts_json_dir, 'all.json')
    with open(posts_json_all_filename, 'w') as f:
        f.write(json.dumps(json_posts, ensure_ascii=False, indent=2))


def create_comments_json_all_file(comments_xml_dir, comments_json_dir):
    all_comments = []

    # Get usermap, mapping integer id to username of commentor
    usermap_json_filename = os.path.join(comments_json_dir, "usermap.json")
    with open(usermap_json_filename) as f:
        users = json.load(f)

    xml_files = find_files_by_pattern('comment_body*.xml', comments_xml_dir)
    for xml_file in xml_files:
        with open(xml_file, 'rt') as f:
            new_comments = extract_comments_from_xml(f.read(), users)
            all_comments.extend(new_comments)

    comments_json_all_filename = os.path.join(comments_json_dir, "all.json")
    with open(comments_json_all_filename, 'w') as f:
        f.write(json.dumps(all_comments, ensure_ascii=False, indent=2))

    return


def extract_comments_from_xml(xml, user_map):
    comments = []

    for comment_xml in ET.fromstring(xml).iter('comment'):
        comment = {
            'jitemid': int(comment_xml.attrib['jitemid']),
            'id': int(comment_xml.attrib['id']),
            'children': []
        }
        get_comment_property('parentid', comment_xml, comment)
        get_comment_property('posterid', comment_xml, comment)
        get_comment_element('date', comment_xml, comment)
        get_comment_element('subject', comment_xml, comment)
        get_comment_element('body', comment_xml, comment)

        if 'state' in comment_xml.attrib:
            comment['state'] = comment_xml.attrib['state']

        if 'posterid' in comment:
            comment['author'] = user_map.get(str(comment['posterid']), "deleted-user")

        comments.append(comment)

    return comments


def download_comments(comments_xml_dir, comments_json_dir):
    comments_metadata_filename = os.path.join(comments_xml_dir, "comment_meta.xml")
    metadata_xml = fetch_xml({'get': 'comment_meta', 'startid': 0})
    with open(comments_metadata_filename, 'w') as f:
        f.write(metadata_xml)

    metadata = ET.fromstring(metadata_xml)
    users = get_users_map(metadata, comments_json_dir)

    start_id = -1
    max_id = int(metadata.find('maxid').text)
    while start_id < max_id:
        start_id, comments = get_more_comments(start_id + 1, users, comments_xml_dir)

    return


def fix_user_links(json_dict):
    """ replace user links with usernames """
    if 'subject' in json_dict:
        json_dict['subject'] = USER.sub(r'\1', json_dict['subject'])

    if 'body' in json_dict:
        json_dict['body'] = USER.sub(r'\1', json_dict['body'])


def json_to_html(json_dict):
    return """<!doctype html>
<meta charset="utf-8">
<title>{subject}</title>
<article>
<h1>{subject}</h1>
{body}
</article>
""".format(
        subject=json_dict['subject'] or json_dict['date'],
        body=TAGLESS_NEWLINES.sub('<br>\n', json_dict['body'])
    )


def get_slug(json_dict):
    slug = json_dict['subject']
    if not len(slug):
        slug = json_dict['id']

    if '<' in slug or '&' in slug:
        slug = BeautifulSoup('<p>{0}</p>'.format(slug)).text

    slug = re.compile(r'\W+').sub('-', slug)
    slug = re.compile(r'^-|-$').sub('', slug)

    if slug in SLUGS:
        slug += (len(slug) and '-' or '') + json_dict['id']

    SLUGS[slug] = True

    return slug


def json_to_markdown(json_dict):
    body = TAGLESS_NEWLINES.sub('<br>', json_dict['body'])

    h = html2text.HTML2Text()
    h.body_width = 0
    h.unicode_snob = True
    body = h.handle(body)
    body = NEWLINES.sub('\n\n', body)

    # read UTX tags
    tags = TAG.findall(body)
    json_dict['tags'] = len(tags) and '\ntags: {0}'.format(', '.join(tags)) or ''

    # remove UTX tags from text
    json_dict['body'] = TAG.sub('', body).strip()

    json_dict['slug'] = get_slug(json_dict)
    json_dict['subject'] = json_dict['subject'] or json_dict['date']

    return """id: {id}
title: {subject}
slug: {slug}
date: {date}{tags}

{body}
""".format(**json_dict)


def group_comments_by_post(comments):
    posts = {}

    for comment in comments:
        post_id = comment['jitemid']

        if post_id not in posts:
            posts[post_id] = {}

        post = posts[post_id]
        post[comment['id']] = comment

    return posts


def nest_comments(comments):
    post = []

    for comment in comments.values():
        fix_user_links(comment)

        if 'parentid' not in comment:
            post.append(comment)
        else:
            comments[comment['parentid']]['children'].append(comment)

    return post


def comment_to_li(comment):
    if 'state' in comment and comment['state'] == 'D':
        return ''

    html = '<h3>{0}: {1}</h3>'.format(comment.get('author', 'anonym'), comment.get('subject', ''))
    html += '\n<a id="comment-{0}"></a>'.format(comment['id'])

    if 'body' in comment:
        html += '\n' + markdown.markdown(TAGLESS_NEWLINES.sub('<br>\n', comment['body']))

    if len(comment['children']) > 0:
        html += '\n' + comments_to_html(comment['children'])

    subject_class = 'subject' in comment and ' class=subject' or ''
    return '<li{0}>{1}\n</li>'.format(subject_class, html)


def comments_to_html(comments):
    return '<ul>\n{0}\n</ul>'.format('\n'.join(map(comment_to_li, sorted(comments, key=itemgetter('id')))))


def save_as_json(json_id, json_post, post_comments, posts_json_dir):
    json_data = {'id': json_id, 'post': json_post, 'comments': post_comments}
    json_filename = os.path.join(posts_json_dir, '{0}.json'.format(json_id))
    with open(json_filename, 'w') as json_file:
        json_file.write(json.dumps(json_data, ensure_ascii=False, indent=2))


def save_as_markdown(markdown_id, subfolder, json_post, post_comments_html, posts_markdown_dir, comments_markdown_dir):
    os.makedirs(os.path.join(posts_markdown_dir, subfolder), exist_ok=True)
    md_filename = os.path.join(posts_markdown_dir, subfolder, markdown_id + ".md")
    with open(md_filename, 'w') as md_file:
        md_file.write(json_to_markdown(json_post))

    if post_comments_html:
        os.makedirs(os.path.join(comments_markdown_dir, subfolder), exist_ok=True)
        md_comments_filename = os.path.join(comments_markdown_dir, json_post['slug'], ".md")
        with open(md_comments_filename, 'w') as md_file:
            md_file.write(post_comments_html)


def save_as_html(html_id, subfolder, json_post, post_comments_html, posts_html_dir):
    os.makedirs(os.path.join(posts_html_dir, subfolder), exist_ok=True)
    html_filename = os.path.join(posts_html_dir, subfolder, html_id + ".html")
    with open(html_filename, 'w') as html_file:
        html_file.writelines(json_to_html(json_post))
        if post_comments_html:
            html_file.write('\n<h2>Comments</h2>\n' + post_comments_html)


def combine(posts, comments, export_dirs):
    posts_comments = group_comments_by_post(comments)

    for json_post in posts:
        post_id = json_post['id']
        jitemid = int(post_id) >> 8

        date = datetime.strptime(json_post['date'], '%Y-%m-%d %H:%M:%S')
        subfolder = '{0.year}-{0.month:02d}'.format(date)

        post_comments = jitemid in posts_comments and nest_comments(posts_comments[jitemid]) or None
        post_comments_html = post_comments and comments_to_html(post_comments) or ''

        fix_user_links(json_post)

        save_as_json(post_id, json_post, post_comments, export_dirs['posts-json'])
        save_as_html(post_id, subfolder, json_post, post_comments_html, export_dirs['posts-html'])
        save_as_markdown(post_id, subfolder, json_post, post_comments_html, export_dirs['posts-markdown'],
                         export_dirs['comments-markdown'])


# Downloads for posts

def fetch_month_posts(year, month):
    response = requests.post(
        'http://www.livejournal.com/export_do.bml',
        headers=config.header,
        cookies=get_cookies(),
        data={
            'what': 'journal',
            'year': year,
            'month': '{0:02d}'.format(month),
            'format': 'xml',
            'header': 'on',
            'encid': '2',
            'field_itemid': 'on',
            'field_eventtime': 'on',
            'field_logtime': 'on',
            'field_subject': 'on',
            'field_event': 'on',
            'field_security': 'on',
            'field_allowmask': 'on',
            'field_currents': 'on'
        }
    )

    return response.text


def xml_to_json(xml):
    def f(field):
        return xml.find(field).text

    return {
        'id': f('itemid'),
        'date': f('logtime'),
        'subject': f('subject') or '',
        'body': f('event'),
        'eventtime': f('eventtime'),
        'security': f('security'),
        'allowmask': f('allowmask'),
        'current_music': f('current_music'),
        'current_mood': f('current_mood')
    }


def download_posts(posts_xml_dir):
    for year in config.years_to_download:
        for month in range(1, 13):
            print("Fetching for " + str(month) + ", " + str(year))
            xml = fetch_month_posts(year, month)
            posts_xml_filename = os.path.join(posts_xml_dir, '{0}-{1:02d}.xml'.format(year, month))
            with open(posts_xml_filename, 'w+') as file:
                file.write(xml)
    return


# Comments
def fetch_xml(params):
    response = requests.get(
        'http://www.livejournal.com/export_comments.bml',
        params=params,
        headers=config.header,
        cookies=get_cookies()
    )

    return response.text


def get_users_map(xml, comments_json_dir):
    users = {}

    for user in xml.iter('usermap'):
        users[user.attrib['id']] = user.attrib['user']

    with open(os.path.join(comments_json_dir, 'usermap.json'), 'w') as f:
        f.write(json.dumps(users, ensure_ascii=False, indent=2))

    return users


def get_comment_property(name, comment_xml, comment):
    if name in comment_xml.attrib:
        comment[name] = int(comment_xml.attrib[name])


def get_comment_element(name, comment_xml, comment):
    elements = comment_xml.findall(name)
    if len(elements) > 0:
        comment[name] = elements[0].text


def get_more_comments(start_id, users, comments_xml_dir):
    comments = []
    local_max_id = -1

    xml = fetch_xml({'get': 'comment_body', 'startid': start_id})
    comments_xml_filename = os.path.join(comments_xml_dir, 'comment_body-{0}.xml'.format(start_id))
    with open(comments_xml_filename, 'w') as f:
        f.write(xml)

    for comment_xml in ET.fromstring(xml).iter('comment'):
        comment = {
            'jitemid': int(comment_xml.attrib['jitemid']),
            'id': int(comment_xml.attrib['id']),
            'children': []
        }
        get_comment_property('parentid', comment_xml, comment)
        get_comment_property('posterid', comment_xml, comment)
        get_comment_element('date', comment_xml, comment)
        get_comment_element('subject', comment_xml, comment)
        get_comment_element('body', comment_xml, comment)

        if 'state' in comment_xml.attrib:
            comment['state'] = comment_xml.attrib['state']

        if 'posterid' in comment:
            comment['author'] = users.get(str(comment['posterid']), "deleted-user")

        local_max_id = max(local_max_id, comment['id'])
        comments.append(comment)

    return local_max_id, comments


# Authentication
def get_cookies():
    r1 = requests.post(config.lj_server + "/interface/flat", data={'mode': 'getchallenge'})
    r1_flat = flatten_string_pairs_to_dict(r1.text)
    challenge = r1_flat['challenge']

    r2 = requests.post(config.lj_server + "/interface/flat",
                       data={'mode': 'sessiongenerate',
                             'user': config.username,
                             'auth_method': 'challenge',
                             'auth_challenge': challenge,
                             'auth_response': make_md5_from_challenge(challenge)
                             }
                       )

    r2_flat = flatten_string_pairs_to_dict(r2.text)

    if r2_flat.get('ljsession', False):
        return {'ljsession': r2_flat['ljsession']}
    else:
        print("Did not get ljsession cookie.  Exiting")
        sys.exit(1)


def flatten_string_pairs_to_dict(response, delimiter='\n'):
    items = response.strip(delimiter).split(delimiter)
    flat_response = {items[i]: items[i + 1] for i in range(0, len(items), 2)}
    return flat_response


def make_md5_from_challenge(challenge):
    first_encoded = challenge + md5(config.password.encode('utf-8')).hexdigest()
    full_encoded = md5(first_encoded.encode('utf-8')).hexdigest()
    return full_encoded


if __name__ == '__main__':
    main()
