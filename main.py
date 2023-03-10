import json
import re
from typing import Any

import aiohttp as aiohttp

import asyncio
from urllib.parse import quote

from _jsonnet import evaluate_snippet

import formats
import regexes
import specs
from app_parser import get_app_info


PLAY_STORE_BASE_URL = "https://play.google.com"


def more_result_section(dataset):
    try:
        return specs.nested_lookup(dataset, ['ds:4', 0, 1])
    except Exception as _exc:
        return None


async def create_link(query_string, n_hits: int = 30, lang: str = "en",
                      country: str = "us"):
    query = quote(query_string)
    url = formats.search_results.build(query, lang, country)
    return url


async def get_dom(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            res = await resp.text()
            return res


def parse_service_data(dom):
    matches = regexes.SERVICE_DATA.findall(dom)
    if not matches:
        return {}
    data = matches[0]
    try:
        # print(data)
        res = re.search(r"{'ds:[\s\S]*}}", data)
        # print(ast.literal_eval(res.group()))
        parsed = evaluate_snippet('snippet', res.group())
        return parsed
    except Exception as _exc:
        return {}


def process_data(data: str):
    try:
        data = data[5:]
        data = json.loads(data)
        data = json.loads(data[0][2])
    except TypeError:
        return None
    return data


async def check_finished(saved_apps: list[dict[str, Any]] | None,
                         token=None, apps_count: int = 100, opts=None):
    if not token:
        return saved_apps or []
    if not opts:
        opts = {
            'term': 'sport',
            'lang': 'en',
            'country': 'us',
        }
    body = f'f.req=%5B%5B%5B%22qnKhOb%22%2C%22%5B%5B' \
           f'null%2C%5B%5B10%2C%5B10%2C{apps_count}%5D%5D%2Ctrue%2Cnull' \
           f'%2C%5B96%2C27%2C4%2C8%2C57%2C30%2C110%2C79%2C11%2C16%2C49%2C1' \
           f'%2C3%2C9%2C12%2C104%2C55%2C56%2C51%2C10%2C34%2C77%5D%5D%2Cnul' \
           f'l%2C%5C%22{token}%5C%22%5D%5D%22%2Cnull%2C%22gen' \
           f'eric%22%5D%5D%5D'
    url = f'{PLAY_STORE_BASE_URL}/_/PlayStoreUi/data/batchexecute?' \
          f'rpcids=qnKhOb&f.sid=-697906427155521722&bl=boq_playuiserver' \
          f'_20190903.08_p0&hl={opts.get("lang")}&gl={opts.get("country")}' \
          f'&authuser&soc-app=121&soc-platform=1&soc-device=1&_reqid=1065213'
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=body, headers={
            'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'
        }) as resp:
            # print(resp.request_info.headers)
            res = await resp.text()
            data = process_data(res)
            if not data:
                return saved_apps or []
            return await process_pages(data, saved_apps)


MAPPINGS = {
    'title': [2],
    'appId': [12, 0],
    'url': {
        'path': [9, 4, 2],
        'fun': lambda url: PLAY_STORE_BASE_URL + url
    },
    'icon': [1, 1, 0, 3, 2],
    'developer': [4, 0, 0, 0],
    'developerId': {
        'path': [4, 0, 0, 1, 4, 2],
        'fun': lambda link: link.split('?id=')[1]
    },
    'price_text': {
        'path': [7, 0, 3, 2, 1, 0, 2],
        'fun': lambda price: 'FREE' if price is None else price
    },
    'currency': [7, 0, 3, 2, 1, 0, 1],
    'price': {
        'path': [7, 0, 3, 2, 1, 0, 2],
        'fun': lambda price: 0 if price is None else float(
            re.search(r"([0-9.,]+)", price).group())
    },
    'summary': [4, 1, 1, 1, 1],
    'scoreText': [6, 0, 2, 1, 0],
    'score': [6, 0, 2, 1, 1]
}


def extract_data_from_app(el):
    res = {}
    for key, spec_value in MAPPINGS.items():
        if isinstance(spec_value, list):
            res[key] = specs.nested_lookup(el, spec_value, True)
        else:
            res[key] = spec_value['fun'](
                specs.nested_lookup(el, spec_value['path'], True))

    return res


def extract_app_list(data):
    data = specs.nested_lookup(data, [0, 0, 0])
    res = []
    if not data:
        return []
    for el in data:
        res.append(extract_data_from_app(el))
    return res


async def process_pages(data, saved_apps, opts=None):
    app_list = extract_app_list(data)
    token = specs.nested_lookup(data, [0, 0, 7], True)
    return await check_finished([*saved_apps, *app_list], token)


async def parse_urls(url: str | list[str]):
    n_hits = 250

    dom = await get_dom(url)
    service_data = parse_service_data(dom)
    matches = regexes.SCRIPT.findall(dom)
    dataset = {}
    for match in matches:
        key_match = regexes.KEY.findall(match)
        value_match = regexes.VALUE.findall(match)

        if key_match and value_match:
            key = key_match[0]
            value = json.loads(value_match[0])
            dataset[key] = value
    success = False
    res_dataset = dataset
    # different idx for different countries and languages
    for idx in range(len(dataset["ds:4"][0][1])):
        try:
            # json.dump(dataset, open('dataset.json', 'w+'))
            dataset = dataset["ds:4"][0][1][idx][22][0]
            success = True
        except Exception:
            pass
    if not success:
        return []

    n_apps = min(len(dataset), n_hits)
    search_results = []
    for app_idx in range(n_apps):
        app = {}
        for k, spec in specs.ElementSpecs.Searchresult.items():
            content = spec.extract_content(dataset[app_idx])
            app[k] = content
        search_results.append(app)
    more_section = more_result_section(res_dataset)[0]

    token = specs.nested_lookup(more_section, [22, 1, 3, 1], True)
    return await check_finished(search_results, token)


async def main():
    res = await parse_urls(
        'https://play.google.com/store/search?q=music&c=apps')
    print(f'finded {len(res)} elements to parse')
    coroutines = []
    for app in res:
        # threading.Thread(target=asyncio.run, args=(load_app_info(app),)).start()
        coroutines.append(get_app_info(app.get('appId')))
    parsed = await asyncio.gather(*coroutines)
    parsed = list(filter(None, parsed))
    json.dump(parsed, open('main.json', 'w+'))
    print(f'successfully parsed {len(parsed)} elements')


asyncio.run(main())
