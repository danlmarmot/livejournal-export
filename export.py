#!/usr/bin/python3
# -*- coding: utf-8 -*-

import fnmatch
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as xml_element_tree
from datetime import datetime
from hashlib import md5
from operator import itemgetter
from pathlib import Path
from lxml import etree

import arrow
import html2text
import markdown
import requests

import ljconfig as config
import userpics

# Other constants
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 8.1; rv:10.0) Gecko/20100101 Firefox/10.0'
}

MIME_EXTENSIONS = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}

DOWNLOADED_JOURNALS_DIR = "exported_journals"

# defines the relative location for locating userpics in comments, no leading or trailing /
STATIC_USERPIC_PART = 'static/userpics'

# A list of directories created under the /exported_journals/username/ directory
EXPORT_DIRS = [
    'posts_xml',
    'posts_json',
    'posts_html',
    'posts_markdown',
    'comments_xml',
    'userpics'
]

TAG = re.compile(r'\[!\[(.*?)\]\(http:/\/utx.ambience.ru\/img\/.*?\)\]\(.*?\)')
USER = re.compile(r'<lj user="?(.*?)"?>')
TAGLESS_NEWLINES = re.compile('(?<!>)\n')
NEWLINES = re.compile('(\s*\n){3,}')
SLUGS = {}

global log


def main():
    # Setup export directories for this LJ user
    export_dirs = ensure_export_dirs(DOWNLOADED_JOURNALS_DIR, config.username, EXPORT_DIRS)

    # Get userpics for lj_user's friends
    if True:
        log.info("Getting friends pics")
        get_friend_pics = userpics.get_friends_default_pics_for_user(config.username, copy_dir=export_dirs['userpics'])

        if get_friend_pics.get('status', False) != 'ok':
            log.critical("Something went wrong ' + get_friend_pics.get('reason', ' (unknown reason)")
            sys.exit(1)

    if True:
        log.info("Downloading posts")
        download_posts(export_dirs['posts_xml'])

        log.info("Downloading comments")
        download_comments(export_dirs['comments_xml'], export_dirs['lj_user'])

    # Generate the all.json files from downloaded posts and comments
    if True:
        create_posts_json_all_file(export_dirs['posts_xml'], export_dirs['lj_user'])
        create_comments_json_all_file(export_dirs['comments_xml'], export_dirs['lj_user'])

    if True:
        with open(os.path.join(export_dirs['lj_user'], 'all_posts.json'), 'r') as f:
            all_posts = json.load(f)
        with open(os.path.join(export_dirs['lj_user'], 'all_comments.json'), 'r') as f:
            all_comments = json.load(f)

        combine(all_posts, all_comments, export_dirs)


def ensure_export_dirs(top_dir, lj_user, ensure_dirs):
    # make sure the lj_user directory exists
    export_dirs = {
        "lj_user": os.path.join(top_dir, lj_user)
    }

    for e in ensure_dirs:
        export_dirs[e] = os.path.join(top_dir, lj_user, e)

    for k, v in export_dirs.items():
        os.makedirs(v, exist_ok=True)

    return export_dirs


def find_files_by_pattern(filepat, top_dir):
    for path, dirlist, filelist in os.walk(top_dir):
        for name in fnmatch.filter(filelist, filepat):
            yield os.path.join(path, name)


def create_posts_json_all_file(posts_xml_dir, lj_user_dir):
    xml_posts = []

    xml_files = find_files_by_pattern('*.xml', posts_xml_dir)
    for xml_file in xml_files:
        with open(xml_file, 'rt') as f:
            xml_posts.extend(list(xml_element_tree.fromstring(f.read()).iter('entry')))

    json_posts = list(map(post_xml_to_json, xml_posts))
    posts_json_all_filename = os.path.join(lj_user_dir, 'all_posts.json')
    with open(posts_json_all_filename, 'w') as f:
        f.write(json.dumps(json_posts, ensure_ascii=False, indent=2))


def create_comments_json_all_file(comments_xml_dir, lj_user_dir):
    all_comments = []

    # Get usermap, mapping integer id to username of commentor
    usermap_json_filename = os.path.join(lj_user_dir, "comments_user_map.json")
    with open(usermap_json_filename) as f:
        users = json.load(f)

    xml_files = find_files_by_pattern('comment_body*.xml', comments_xml_dir)
    for xml_file in xml_files:
        with open(xml_file, 'rt') as f:
            new_comments = extract_comments_from_xml(f.read(), users)
            all_comments.extend(new_comments)

    comments_json_all_filename = os.path.join(lj_user_dir, "all_comments.json")
    with open(comments_json_all_filename, 'w') as f:
        f.write(json.dumps(all_comments, ensure_ascii=False, indent=2))

    return


def extract_comments_from_xml(xml, user_map):
    comments = []

    for comment_xml in xml_element_tree.fromstring(xml).iter('comment'):
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


# Comment metadata is paged, and used to build the usermap
def download_comment_metadata(comments_xml_dir, lj_user_dir):
    log.info(f"Fetching comment metadata, starting at comment {str(start_id)}")

    for metadata_xml in get_comment_metadata_xml(comments_xml_dir):
        update_users_map(metadata_xml, lj_user_dir)


def get_comment_metadata_xml(comments_xml_dir, start_id=0):
    log.info("Fetching comment metadata for usermap")
    metadata_file = Path(comments_xml_dir, f'comment_meta-{str(start_id)}.xml')

    if metadata_file.is_file():
        # metadata downloaded, read it in

        log.info(f"  Reading local file for metadata: comment_meta-{str(start_id)}.xml")
        with open(metadata_file, 'rb') as f:
            root = etree.XML(f.read())

    else:

        log.info(f"  Downloading file for comment_meta-{str(start_id)}.xml")
        response = requests.get(
            'http://www.livejournal.com/export_comments.bml',
            params={'get': 'comment_meta', 'startid': start_id},
            headers=config.header,
            cookies=get_cookies()
        )

        if requests.codes.ok:
            with open(metadata_file, 'w') as f:
                f.write(response.text)
                # note r.content used, not r.text, to avoid encoding mismatch error from lxml
                root = etree.XML(response.content)

    yield root

    max_id = root.findtext('maxid')
    next_id = root.findtext('nextid')
    if next_id and (int(next_id) < int(max_id)):
        yield from get_comment_metadata_xml(comments_xml_dir, start_id=next_id)


def download_comments(comments_xml_dir, lj_user_dir):
    # Get users from usermap file
    users = get_users_map(comments_xml_dir, lj_user_dir)

    log.info("Fetching comment max_id")
    metadata_file = Path(comments_xml_dir, f'comment_meta-0.xml')
    if not metadata_file.is_file():
        get_comment_metadata_xml(comments_xml_dir)

    with open(metadata_file, 'rb') as f:
        root = etree.XML(f.read())
        max_id = root.findtext('maxid')
        del root

    start_id = -1
    while start_id < int(max_id):
        start_id, comments = get_more_comments(start_id + 1, users, comments_xml_dir)
        time.sleep(8)

    return


def get_users_map(comments_xml_dir, lj_user_dir, force=False):
    usermap_file = Path(lj_user_dir, 'comments_user_map.json')

    # Create it if not present, or forced
    if not usermap_file.is_file() or force:
        for metadata_xml in get_comment_metadata_xml(comments_xml_dir):
            update_users_map(metadata_xml, lj_user_dir)

    # Read it in
    with open(usermap_file, 'r') as f:
        users_map = json.load(f)

    return users_map


def fix_user_links(json_dict):
    """ replace user links with usernames """
    if 'subject' in json_dict:
        json_dict['subject'] = USER.sub(r'\1', json_dict['subject'])

    if 'body' in json_dict:
        json_dict['body'] = USER.sub(r'\1', json_dict['body'])


def post_json_to_html(json_dict):
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
    slug = json_dict.get('subject', json_dict['id'])
    if not len(slug):
        slug = json_dict['id']

    slug = slug.lower()

    # change everything not in [a-zA-Z0-9] to -
    slug = re.compile(r'\W+|_').sub('-', slug)

    # remove leading and trailing -
    slug = re.compile(r'^-|-$').sub('', slug)

    # remove multi-dashes
    slug = re.compile(r'-+').sub('-', slug)

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

    # json_dict['slug'] = get_slug(json_dict)
    json_dict['subject'] = json_dict['subject'] or json_dict['date']

    md_text = """id: {id}
Title: {subject}
Date: {date}
Tags: {tags}
Status: published
Slug: {slug}

Security (from LJ): {security}

{body}
""".format(**json_dict)

    return md_text


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

    html = '<h3>{0}: {1}</h3>'.format(comment.get('author', 'Anonymous'), comment.get('subject', ''))
    html += '\n<a id="comment-{0}"></a>'.format(comment['id'])

    if 'body' in comment:
        html += '\n' + markdown.markdown(TAGLESS_NEWLINES.sub('<br>\n', comment['body']))

    if len(comment['children']) > 0:
        html += '\n' + comments_to_html(comment['children'])

    subject_class = 'subject' in comment and ' class=subject' or ''
    return '<li{0}>{1}\n</li>'.format(subject_class, html)


def make_md_comment(comment, export_dirs, level=0):
    """
    For static site generators like Pelican.
    See http://docs.getpelican.com/en/stable/content.html#file-metadata for details

    Relies on python-markdown extension for adding classes via attribute lists
    https://pythonhosted.org/Markdown/extensions/attr_list.html
    """

    # Ensure the userpic is present, or use the default one
    commenting_user = comment.get('author', 'anonymous')

    userpic = userpics.get_userpic(commenting_user, copy_dir=export_dirs['userpics'])
    userpic_file = userpic.get('filename', None)
    if userpic_file is None:
        userpic_file = userpics.DEFAULT_USERPIC_FILE

    md = ''
    if 'state' in comment and comment['state'] == 'D':
        return ''

    comment_date_str = arrow.get(comment['date']).format('MMMM D YYYY, HH:mm:ss')

    # Full container for comment
    md += '<div class=lj-comment-wrap style="margin-left:' + str(level * 25) + 'px;">\n'

    # Top of comment bar: userpic, username, posting time
    md += "<div class=lj-comment-head>\n"  # Userpic.

    md += "<div class=lj-comment-userpic>\n"
    img_src = '<img src="/' + STATIC_USERPIC_PART + '/' + userpic_file + '" width="100" height="100">\n'
    md += img_src
    md += "</div>\n"

    # Comment ljusername and datetime
    md += "<div class=lj-comment-head-in>\n"
    md += "<div><span class=lj-comment-user>" + commenting_user + "</span></div>\n"

    md += "<div><span class=lj-comment-datetime>" + comment_date_str + "</span></div>\n"
    # Bottom of comment bar

    md += "</div>\n"  # close lj-comment-head-in
    md += "</div>\n"  # close lj-comment-head

    if 'body' in comment:
        md += "<div class=lj-comment-text>\n"
        md_body = markdown.markdown(TAGLESS_NEWLINES.sub('<br>\n', comment['body']))
        # print(comment['body'])
        md += md_body
        md += "</div>\n"

    # Close comment container
    md += "</div>\n\n"

    # Children aren't nested, but are rather indented via their class attributes.
    if len(comment['children']) > 0:
        sorted_children = sorted(comment['children'], key=itemgetter('id'))
        child_comments = [make_md_comment(c, export_dirs, level + 1) for c in sorted_children]
        md += '\n'.join(child_comments)

    # print(comment.get('author', 'anonymous')+"\n------------")

    rv_md = markdown.markdown(md, ['markdown.extensions.extra'])
    # print(rv_md)

    return rv_md


def comments_to_html(comments):
    return '<ul>\n{0}\n</ul>'.format('\n'.join(map(comment_to_li, sorted(comments, key=itemgetter('id')))))


def comments_to_md(comments, export_dirs):
    rv = "<hr>\n"
    rv += "###Comments\n\n"
    sorted_comments = sorted(comments, key=itemgetter('id'))
    md_comments = [make_md_comment(c, export_dirs) for c in sorted_comments]
    rv += '\n'.join(md_comments)
    return rv


def save_as_json(json_post, post_comments, posts_json_dir):
    json_id = json_post['id']
    json_data = {'id': json_id, 'post': json_post, 'comments': post_comments}
    json_filename = os.path.join(posts_json_dir, '{0}.json'.format(json_id))
    with open(json_filename, 'w') as json_file:
        json_file.write(json.dumps(json_data, ensure_ascii=False, indent=2))


def save_as_markdown(json_post, subfolder, post_comments_md,
                     posts_markdown_dir):
    parent_md_dir = os.path.join(posts_markdown_dir, subfolder)
    os.makedirs(parent_md_dir, exist_ok=True)

    md_filename = os.path.join(parent_md_dir, json_post['slug'] + ".md")
    with open(md_filename, 'w') as md_file:
        md_file.write(json_to_markdown(json_post))

        if post_comments_md:
            md_file.write('\n' + post_comments_md)


def save_as_html(json_post, subfolder, post_comments_html, posts_html_dir):
    post_id = json_post['id']

    parent_dir = os.path.join(posts_html_dir, subfolder)
    os.makedirs(parent_dir, exist_ok=True)

    html_filename = os.path.join(parent_dir, post_id + ".html")
    with open(html_filename, 'w') as html_file:
        html_file.writelines(post_json_to_html(json_post))
        if post_comments_html:
            html_file.write('\n<h2>Comments</h2>\n' + post_comments_html)

            # if post_comments_html:
            #     parent_comments_dir = os.path.join(comments_html_dir, year_dir, month_dir)
            #     os.makedirs(parent_comments_dir, exist_ok=True)
            #
            #     html_comments_filename = os.path.join(parent_comments_dir, json_post['slug'] + ".html")
            #     with open(html_comments_filename, 'w') as f:
            #         f.write(post_comments_html)


def combine(posts, comments, export_dirs):
    posts_comments = group_comments_by_post(comments)

    num_posts = len(posts)

    start_time = datetime.now()
    for i, json_post in enumerate(posts):
        post_id = json_post['id']
        log.info(f'Generating post for {json_post["date"]}, {i+1} of {num_posts}')

        jitemid = int(post_id) >> 8

        date = datetime.strptime(json_post['date'], '%Y-%m-%d %H:%M:%S')
        subfolder_year = '{0.year}'.format(date)
        subfolder_month = '{0.month:02d}'.format(date)
        subfolder = os.path.join(subfolder_year, subfolder_month)

        post_comments = jitemid in posts_comments and nest_comments(posts_comments[jitemid]) or None
        post_comments_html = post_comments and comments_to_html(post_comments) or ''
        post_comments_md = post_comments and comments_to_md(post_comments, export_dirs) or ''

        fix_user_links(json_post)
        json_post['slug'] = get_slug(json_post)

        save_as_json(json_post,
                     post_comments,
                     export_dirs['posts_json'])

        save_as_html(json_post,
                     subfolder,
                     post_comments_html,
                     export_dirs['posts_html'])

        save_as_markdown(json_post,
                         subfolder,
                         post_comments_md,
                         export_dirs['posts_markdown']
                         )




# Downloads for posts

def fetch_month_posts(year, month):
    response = requests.post(
        'http://www.livejournal.com/export_do.bml',
        headers=config.header,
        cookies=get_cookies(),
        data={
            'what': 'journal',
            'year': year,
            'month': '{0:02d}'.format(int(month)),
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


def post_xml_to_json(xml):
    def f(field):
        return xml.find(field).text

    return {
        'id': f('itemid'),
        'logtime': f('logtime'),
        'subject': f('subject') or '',
        'body': f('event'),
        'date': f('eventtime'),
        'security': f('security'),
        'allowmask': f('allowmask'),
        'current_music': f('current_music'),
        'current_mood': f('current_mood')
    }


def download_posts(posts_xml_dir):
    start_date = config.start_date
    end_date = config.end_date

    years_and_months = [(int(d[0].format('YYYY')), int(d[0].format('M')))
                        for d in arrow.Arrow.span_range('month', arrow.get(start_date), arrow.get(end_date))]

    for year, month in years_and_months:
        posts_xml_filename = Path(posts_xml_dir, f'{year}-{month:02d}.xml')

        if posts_xml_filename.is_file():
            log.info(f"Not downloading posts for {year}-{month:02d}, downloaded already")
            continue

        xml = fetch_month_posts(year, month)
        log.info(f"Downloading posts for {year}-{month:02d}")
        with open(posts_xml_filename, 'w+', encoding="utf8") as file:
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


def update_users_map(root_xml, lj_user_dir):
    incoming_users = {}

    usermap_file = Path(lj_user_dir, 'comments_user_map.json')

    # Read in existing usermaps, if file exists
    if usermap_file.is_file():
        with open(usermap_file, 'r') as f:
            existing_usermaps = json.load(f)
    else:
        existing_usermaps = {}

    # Get user records
    user_records = root_xml.xpath('.//usermap')
    for r in user_records:
        incoming_users[r.attrib['id']] = r.attrib['user']

    # Merge
    updated_users = {**existing_usermaps, **incoming_users}

    # Write it out
    with open(os.path.join(lj_user_dir, 'comments_user_map.json'), 'w') as f:
        f.write(json.dumps(updated_users, ensure_ascii=False, indent=2))

    return


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

    log.info(f"Fetching more comments, now at comment {str(start_id)}")

    xml = fetch_xml({'get': 'comment_body', 'startid': start_id})
    comments_xml_filename = os.path.join(comments_xml_dir, 'comment_body-{0}.xml'.format(start_id))
    with open(comments_xml_filename, 'w', encoding="utf8") as f:
        f.write(xml)

    for comment_xml in xml_element_tree.fromstring(xml).iter('comment'):
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


def setup_logging():
    # for thread debugging, this is helpful:
    # format='%(asctime)5s %(threadName)10s %(name)18s: %(message)s'

    logstr_format = "%(asctime)s | %(levelname)-8s | %(funcName)s:%(lineno)s | %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format=logstr_format,
        datefmt="%Y-%m-%d %H:%M:%S"
    )


if __name__ == '__main__':
    setup_logging()
    log = logging.getLogger()

    main()
