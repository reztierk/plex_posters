#!/usr/bin/env python3

import re
import os
import yaml
import requests
import click
import hashlib
import pprint as pretty
import logging
import json
import math
from plexapi.server import PlexServer

CONFIG_FILE = 'config.yaml'
DEBUG = False
DRY_RUN = False
LIBRARY_IDS = False
CONFIG = dict()
PAGE_SIZE = 50


def init(debug=False, dry_run=False, library_ids=False):
    global DEBUG
    global DRY_RUN
    global LIBRARY_IDS
    global CONFIG

    DEBUG = debug
    DRY_RUN = dry_run
    LIBRARY_IDS = library_ids

    if not DEBUG:
        logging.getLogger('tmdbv3api.tmdb').disabled = True

    with open(CONFIG_FILE, 'r') as stream:
        try:
            CONFIG = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            print(exc)

    CONFIG['headers'] = {'X-Plex-Token': CONFIG['plex_token'], 'Accept': 'application/json'}
    CONFIG['plex_sections_url'] = \
        '%s/library/sections/%%s/all?type=1&X-Plex-Container-Start=%%s&X-Plex-Container-Size=%%s' % CONFIG['plex_url']
    CONFIG['plex_images_url'] = '%s/library/metadata/%%s/%%s' % CONFIG['plex_url']
    CONFIG['plex_images_select_url'] = '%s/library/metadata/%%s/%%s?url=%%s' % CONFIG['plex_url']
    CONFIG['plex_images_upload_url'] = '%s/library/metadata/%%s/%%s?includeExternalMedia=1' % CONFIG['plex_url']

    if DEBUG:
        print('CONFIG: ')
        pretty.pprint(CONFIG)


def setup():
    try:
        data = dict()
        data['plex_url'] = click.prompt('Please enter your Plex URL', type=str)
        data['plex_token'] = click.prompt('Please enter your Plex Token', type=str)
        data['tmdb_key'] = click.prompt('Please enter your TMDB API Key', type=str)

        data['custom_poster_filename'] = click.prompt(
            'Please enter the Custom Poster filename (OPTIONAL)',
            default="poster-custom",
            type=str
        )

        with open(CONFIG_FILE, 'w') as outfile:
            yaml.dump(data, outfile, default_flow_style=False)
    except (KeyboardInterrupt, SystemExit):
        raise


def check():
    plex = PlexServer(CONFIG['plex_url'], CONFIG['plex_token'])
    plex_sections = plex.library.sections()

    for plex_section in plex_sections:
        if plex_section.type != 'movie':
            continue

        if LIBRARY_IDS and int(plex_section.key) not in LIBRARY_IDS:
            print('ID: %s Name: %s - SKIPPED' % (str(plex_section.key).ljust(4, ' '), plex_section.title))
            continue

        print('ID: %s Name: %s' % (str(plex_section.key).ljust(4, ' '), plex_section.title))
        section_total = get_section_count(plex_section.key)
        total_pages = math.ceil(section_total / PAGE_SIZE)
        current_page = 0

        while current_page < total_pages:
            plex_movies = get_plex_data(CONFIG['plex_sections_url']
                                        % (plex_section.key, PAGE_SIZE * current_page, PAGE_SIZE))
            check_posters(plex_movies.get('Metadata'), PAGE_SIZE * current_page, section_total)
            current_page += 1


def list_libraries():
    plex = PlexServer(CONFIG['plex_url'], CONFIG['plex_token'])
    plex_sections = plex.library.sections()

    for plex_section in plex_sections:
        if plex_section.type != 'movie':
            continue

        print('ID: %s Name: %s' % (str(plex_section.key).ljust(4, ' '), plex_section.title))


def get_section_count(section_id):
    section = get_plex_data(CONFIG['plex_sections_url'] % (section_id, 0, 1))
    return int(section.get('totalSize'))


def check_posters(plex_movies, offset, total):
    for i, plex_movie in enumerate(plex_movies):
        print('\r\n> %s [%s/%s]' % (plex_movie.get('title'), offset + i + 1, total))
        if check_custom_poster(plex_movie):
            continue

        check_local_poster(plex_movie.get('ratingKey'))


def check_custom_poster(plex_movie):
    file_path = str(os.path.dirname(plex_movie.get('Media')[0].get('Part')[0].get('file'))) + os.path.sep\
                + str(CONFIG['custom_poster_filename'])
    poster_path = ''

    if os.path.isfile(file_path + '.jpg'):
        poster_path = file_path + '.jpg'
    elif os.path.isfile(file_path + '.png'):
        poster_path = file_path + '.png'

    if poster_path != '':
        if DEBUG:
            print("%s Collection Poster Exists")
        key = get_sha1(poster_path)
        poster_exists = check_if_poster_is_uploaded(key, plex_movie.get('ratingKey'))

        if poster_exists:
            print("Using Custom Poster")
            return True

        if DRY_RUN:
            print("Would Set Custom Poster: %s" % poster_path)
            return True

        requests.post(CONFIG['plex_images_upload_url'] % (plex_movie.get('ratingKey'), 'posters'),
                      data=open(poster_path, 'rb'), headers=CONFIG['headers'])
        print("Custom Poster Set")
        return True


def check_if_poster_is_uploaded(key, movie_id):
    images = get_plex_data(CONFIG['plex_images_url'] % (movie_id, 'posters'))
    key_prefix = 'upload://posters/'
    for image in images.get('Metadata'):
        if image.get('selected'):
            if image.get('ratingKey') == key_prefix + key:
                return True
        if image.get('ratingKey') == key_prefix + key:
            if DRY_RUN:
                print("Would Change Selected Poster to: " + image.get('ratingKey'))
                return True

            requests.put(CONFIG['plex_images_select_url'] % (movie_id, 'poster', image.get('ratingKey')),
                         data={}, headers=CONFIG['headers'])
            return True


def check_local_poster(movie_id):
    images = get_plex_data(CONFIG['plex_images_url'] % (movie_id, 'posters'))
    for image in images.get('Metadata'):
        if image.get('selected'):
            if image.get('provider') == 'com.plexapp.agents.localmedia':
                print('Using Local Poster')
                return

            print('Using Other Poster')
            return

    print('No Poster Selected? Selecting the first poster available')
    requests.put(CONFIG['plex_images_select_url'] % (movie_id, 'poster', images[0].get('ratingKey')),
                 data={}, headers=CONFIG['headers'])


def get_sha1(file_path):
    h = hashlib.sha1()

    with open(file_path, 'rb') as file:
        while True:
            # Reading is buffered, so we can read smaller chunks.
            chunk = file.read(h.block_size)
            if not chunk:
                break
            h.update(chunk)

    return h.hexdigest()


def get_plex_data(url):
    r = requests.get(url, headers=CONFIG['headers'])
    return json.loads(r.text).get('MediaContainer')


@click.group()
def cli():
    if not os.path.isfile(CONFIG_FILE):
        click.confirm('Configuration not found, would you like to set it up?', abort=True)
        setup()
        exit(0)
    pass


@cli.command('setup', help='Set Configuration Values')
def command_setup():
    setup()


@cli.command('run', help='Check Plex Collections for missing movies',
             epilog="eg: plex_collections_missing.py run --dry-run --library=5 --library=8")
@click.option('--debug', '-v', default=False, is_flag=True)
@click.option('--dry-run', '-d', default=False, is_flag=True)
@click.option('--library', default=False, multiple=True, type=int,
              help='Library ID to Update (Default all movie libraries)')
def run(debug, dry_run, library):
    init(debug, dry_run, library)
    print('\r\nChecking Movies(s)')
    check()


@cli.command('list', help='List all Libraries')
def list_all():
    init()
    print('\r\nLibraries:')
    list_libraries()


if __name__ == "__main__":
    cli()
