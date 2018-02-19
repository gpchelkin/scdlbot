#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os

from setuptools import setup, find_packages

module_name = 'scdlbot'


def get_version(version_tuple):
    # additional handling of a,b,rc tags, this can be simpler depending on your versioning scheme
    if not isinstance(version_tuple[-1], int):
        return '.'.join(
            map(str, version_tuple[:-1])
        ) + version_tuple[-1]
    return '.'.join(map(str, version_tuple))


# path to the packages __init__ module in project source tree
init = os.path.join(os.path.dirname(__file__), module_name, '__init__.py')

version_line = list(
    filter(lambda l: l.startswith('VERSION'), open(init))
)[0]

# VERSION is a tuple so we need to eval its line of code.
# We could simply import it from the package but we
# cannot be sure that this package is importable before
# finishing its installation
VERSION = get_version(eval(version_line.split('=')[-1]))

with open('README.rst') as readme_file:
    readme = readme_file.read()

with open('HISTORY.rst') as history_file:
    history = history_file.read()

requirements = [
    "python-telegram-bot",
    "boltons",
    "plumbum",
    # "transliterate",
    "scdl",
    "bandcamp-downloader",
    "youtube_dl",
    "setuptools",
    "pydub",
    "mutagen",
    "patool",
    "pyshorteners",
    "Celery",
    #    "botanio",
    "logentries",
    #    "loggly-python-handler",
    "python-telegram-handler"
]

setup(
    name=module_name,
    version=VERSION,
    description="Telegram Bot for downloading MP3 rips of tracks/sets from SoundCloud, Bandcamp, YouTube with tags and artwork.",
    # long_description=readme + '\n\n' + history,
    long_description=readme,
    author="George Pchelkin",
    author_email='george@pchelk.in',
    url='https://github.com/gpchelkin/scdlbot',
    packages=find_packages(),
    include_package_data=True,
    install_requires=requirements,
    python_requires='~=3.5',
    license="MIT",
    zip_safe=True,
    keywords='scdlbot telegram bot soundcloud bandcamp youtube audio music download',
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: End Users/Desktop',
        'Topic :: Multimedia :: Sound/Audio',
        'Topic :: Internet',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Natural Language :: English',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.5',
        'Programming Language :: Python :: 3.6',
    ],
    entry_points={
        'console_scripts': [
            'scdlbot={}.__main__:main'.format(module_name),
        ],
    },
)
