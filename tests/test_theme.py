"""Theme shell, assets, accessibility colors and dependency-free JS behavior."""
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main

ROOT = Path(__file__).resolve().parents[1]
OBSOLETE_ASSETS = [f'chainloop-icon-{size}.png' for size in ['16', '32', '48', '128', '256', '512', '1024', 'source']] + ['chainloop-strava-124.png']


@pytest.fixture
def client(db_engine, monkeypatch):
    monkeypatch.setattr(main, 'engine', db_engine)
    with TestClient(main.app, base_url='https://testserver') as value:
        yield value


class Tags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


@pytest.mark.parametrize('path', ['/', '/admin', '/statistics', '/history'])
def test_shared_shell_has_native_theme_controls_and_safe_bootstrap(client, path):
    response = client.get(path)
    assert response.status_code == 200
    tags = Tags(response.text).tags
    radios = [attrs for tag, attrs in tags if tag == 'input' and attrs.get('name') == 'theme']
    assert [item['value'] for item in radios] == ['system', 'light', 'dark']
    assert all(item['type'] == 'radio' for item in radios)
    assert ['checked' in item for item in radios] == [True, False, False]
    assert '<legend class="sr-only">Appearance</legend>' in response.text
    scripts = [attrs for tag, attrs in tags if tag == 'script' and attrs.get('src') == '/static/theme.js']
    assert scripts == [{'src': '/static/theme.js'}]  # Blocking head script applies before CSS/paint.
    assert response.text.index('/static/theme.js') < response.text.index('/static/app.css')
    assert 'unsafe-inline' not in response.headers['content-security-policy']
    assert 'unsafe-eval' not in response.headers['content-security-policy']
    assert all('style' not in attrs and not any(key.startswith('on') for key in attrs) for _, attrs in tags)
    assert any(tag == 'a' and attrs.get('href') == main.PROJECT_URL for tag, attrs in tags)
    assert main.PROJECT_URL == 'https://github.com/grayum/ChainLoop'
    assert any(tag == 'a' and attrs.get('href') == 'https://grahamofthewheels.com/' for tag, attrs in tags)
    assert 'Created with assistance from ChatGPT &amp; Codex (OpenAI).' in response.text
    assert any(tag == 'main' and attrs.get('id') == 'main-content' for tag, attrs in tags)
    assert any(tag == 'a' and attrs.get('href') == path and attrs.get('aria-current') == 'page' for tag, attrs in tags)


@pytest.mark.parametrize('asset', ['favicon.ico', 'favicon.png', 'static/branding/chainloop-logo-dark.png', 'static/branding/chainloop-favicon-source.png', 'static/theme.js'])
def test_surviving_assets_resolve(client, asset):
    assert client.get('/' + asset).status_code == 200


def test_obsolete_assets_and_references_are_removed(client):
    readme = (ROOT / 'README.md').read_text()
    application = '\n'.join(path.read_text() for path in (ROOT / 'app').rglob('*') if path.suffix in {'.html', '.css', '.js', '.py'})
    for asset in OBSOLETE_ASSETS:
        assert not (ROOT / 'app/static/branding' / asset).exists()
        assert asset not in readme + application
        assert client.get('/static/branding/' + asset).status_code == 404


def test_visible_form_controls_have_accessible_labels(client, configured_chain):
    class Labels(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = 0
            self.unlabelled = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'label':
                self.depth += 1
            if tag in {'input', 'select', 'textarea'} and attrs.get('type') != 'hidden':
                if not self.depth and not attrs.get('aria-label'):
                    self.unlabelled.append(attrs.get('name'))

        def handle_endtag(self, tag):
            if tag == 'label':
                self.depth -= 1

    for path in ['/', '/admin', f"/chains/{configured_chain['chain_id']}"]:
        response = client.get(path)
        assert response.status_code == 200
        labels = Labels()
        labels.feed(response.text)
        assert labels.unlabelled == [], (path, labels.unlabelled)


@pytest.mark.skipif(shutil.which('node') is None, reason='Node.js 18+ runs the browser-independent theme behavior tests (see README)')
def test_theme_javascript_behavior():
    result = subprocess.run(['node', '--test', str(ROOT / 'tests/theme.test.cjs')], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def luminance(color):
    rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in rgb]
    return sum(value * weight for value, weight in zip(linear, [.2126, .7152, .0722]))


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_semantic_text_and_status_colors_meet_aa_contrast(theme):
    css = (ROOT / 'app/static/app.css').read_text()
    selector = ':root' if theme == 'light' else ':root[data-theme="dark"]'
    base = css.split(':root {', 1)[1].split('}', 1)[0]
    block = css.split(selector + ' {', 1)[1].split('}', 1)[0]
    colors = dict(re.findall(r'(--[\w-]+):\s*(#[0-9a-f]{6});', base + block))
    pairs = [('text', 'bg'), ('text', 'card'), ('muted', 'card'), ('link', 'card'),
             ('text', 'form-bg'), ('ok', 'success-bg'), ('warn', 'warning-bg'),
             ('bad', 'danger-bg'), ('on-accent', 'accent'), ('hero-text', 'hero-bg'),
             ('hero-link', 'hero-bg'), ('chart-text', 'form-bg')]
    for foreground, background in pairs:
        light, dark = sorted([luminance(colors['--' + foreground]), luminance(colors['--' + background])], reverse=True)
        assert (light + .05) / (dark + .05) >= 4.5, (theme, foreground, background)
    for index in range(6):
        light, dark = sorted([luminance(colors[f'--chart-{index}']), luminance(colors['--form-bg'])], reverse=True)
        assert (light + .05) / (dark + .05) >= 3, (theme, index)
    for foreground, background in [('border', 'form-bg'), ('focus', 'card'), ('chart-axis', 'form-bg')]:
        light, dark = sorted([luminance(colors['--' + foreground]), luminance(colors['--' + background])], reverse=True)
        assert (light + .05) / (dark + .05) >= 3, (theme, foreground)
