# -*- coding: utf-8 -*-

"""Tests for `scdlbot` package."""

import pytest

from scdlbot import scdlbot


@pytest.fixture
def response():
    """Sample pytest fixture.

    See more at: http://doc.pytest.org/en/latest/fixture.html
    """
    # import requests
    # return requests.get('https://github.com/audreyr/cookiecutter-pypackage')


def test_content(response):
    """Sample pytest test function with the pytest fixture as an argument."""
    # from bs4 import BeautifulSoup
    # assert 'GitHub' in BeautifulSoup(response.content).title.string


#@pytest.mark.parametrize(('first', 'second', 'expected'), [
#    (1, 2, 3),
#    (2, 4, 6),
#    (-2, -3, -5),
#    (-5, 5, 0),
#])
#def test_some_function(first, second, expected):
#    """Example test with parametrization."""
#    assert some_function(first, second) == expected

