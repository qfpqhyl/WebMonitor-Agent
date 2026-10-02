from bs4 import BeautifulSoup
from webmonitor.collection.coverage import rows_complete,full_list_evidence
from webmonitor.collection.types import analyze_html


def test_declared_total_requires_every_unique_position():
    complete='<ul id="items"><li aria-setsize="2" aria-posinset="1">A</li><li aria-setsize="2" aria-posinset="2">B</li></ul>'
    assert rows_complete(BeautifulSoup(complete,'lxml').select('li'))
    assert full_list_evidence(complete)[0]['declared_total']==2
    partial='<ul><li aria-setsize="2" aria-posinset="1">A</li></ul>'
    assert not rows_complete(BeautifulSoup(partial,'lxml').select('li'))
    duplicate=complete.replace('aria-posinset="2"','aria-posinset="1"')
    assert not full_list_evidence(duplicate)
    unknown='<ul><li>A</li><li>B</li></ul>'
    assert not full_list_evidence(unknown)


def test_pagination_control_is_bounded_but_next_app_root_is_not_pagination():
    _,warnings=analyze_html('<div id="__next"><h1>Product</h1></div>')
    assert 'pagination_coverage_unproven' not in warnings
    _,warnings=analyze_html('<button id="next-page">Load next page</button>')
    assert 'pagination_coverage_unproven' in warnings
