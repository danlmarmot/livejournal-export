#!/usr/bin/python3

import json
import os
import re
import html2text
import markdown
from bs4 import BeautifulSoup
from datetime import datetime
from operator import itemgetter
from download_posts import download_posts
from download_comments import download_comments

TAG = re.compile(r'\[!\[(.*?)\]\(http:\/\/utx.ambience.ru\/img\/.*?\)\]\(.*?\)')
USER = re.compile(r'<lj user="?(.*?)"?>')
TAGLESS_NEWLINES = re.compile('(?<!>)\n')
NEWLINES = re.compile('(\s*\n){3,}')
SLUGS = {}


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


def save_as_json(json_id, json_post, post_comments):
    json_data = {'id': json_id, 'post': json_post, 'comments': post_comments}
    with open('posts-json/{0}.json'.format(json_id), 'w') as json_file:
        json_file.write(json.dumps(json_data, ensure_ascii=False, indent=2))


def save_as_markdown(markdown_id, subfolder, json_post, post_comments_html):
    os.makedirs('posts-markdown/{0}'.format(subfolder), exist_ok=True)
    with open('posts-markdown/{0}/{1}.md'.format(subfolder, markdown_id), 'w') as md_file:
        md_file.write(json_to_markdown(json_post))
    if post_comments_html:
        with open('comments-markdown/{0}.md'.format(json_post['slug']), 'w') as md_file:
            md_file.write(post_comments_html)


def save_as_html(html_id, subfolder, json_post, post_comments_html):
    os.makedirs('posts-html/{0}'.format(subfolder), exist_ok=True)
    with open('posts-html/{0}/{1}.html'.format(subfolder, html_id), 'w') as html_file:
        html_file.writelines(json_to_html(json_post))
        if post_comments_html:
            html_file.write('\n<h2>Комментарии</h2>\n' + post_comments_html)


def combine(posts, comments):
    os.makedirs('posts-html', exist_ok=True)
    os.makedirs('posts-markdown', exist_ok=True)
    os.makedirs('comments-markdown', exist_ok=True)

    posts_comments = group_comments_by_post(comments)

    for json_post in posts:
        post_id = json_post['id']
        jitemid = int(post_id) >> 8

        date = datetime.strptime(json_post['date'], '%Y-%m-%d %H:%M:%S')
        subfolder = '{0.year}-{0.month:02d}'.format(date)

        post_comments = jitemid in posts_comments and nest_comments(posts_comments[jitemid]) or None
        post_comments_html = post_comments and comments_to_html(post_comments) or ''

        fix_user_links(json_post)

        save_as_json(post_id, json_post, post_comments)
        save_as_html(post_id, subfolder, json_post, post_comments_html)
        save_as_markdown(post_id, subfolder, json_post, post_comments_html)


if __name__ == '__main__':
    if True:
        all_posts = download_posts()
        all_comments = download_comments()

    else:
        with open('posts-json/all.json', 'r') as f:
            all_posts = json.load(f)
        with open('comments-json/all.json', 'r') as f:
            all_comments = json.load(f)

    combine(all_posts, all_comments)
