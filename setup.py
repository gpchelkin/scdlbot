#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from setuptools import setup

with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

requirements = [
    "python-telegram-bot",
    "boltons",
    # "transliterate",
    "plumbum",
    "scdl",
    "bandcamp-downloader",
    "youtube_dl",
    "setuptools",
    "pydub",
    "mutagen",
]

setup(
    name='scdlbot',
    version='0.3.1',
    description="Downloads MP3 rips of tracks/sets from SoundCloud, Bandcamp, YouTube with tags and artwork.",
    long_description=readme + '\n\n' + history,
    author="George Pchelkin",
    author_email='george@pchelk.in',
    url='https://github.com/gpchelkin/scdlbot',
    packages=[
        'scdlbot',
    ],
    package_dir={'scdlbot':
                 'scdlbot'},
    include_package_data=True,
    install_requires=requirements,
    license="GNU General Public License v3",
    zip_safe=True,
    keywords='scdlbot',
    classifiers=[
        'Development Status :: 2 - Pre-Alpha',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.3',
        'Programming Language :: Python :: 3.4',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
)
