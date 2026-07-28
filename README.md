# Repository Coverage

[Full report](https://htmlpreview.github.io/?https://github.com/FrostWillmott/Relay/blob/python-coverage-comment-action-data/htmlcov/index.html)

| Name                          |    Stmts |     Miss |   Cover |   Missing |
|------------------------------ | -------: | -------: | ------: | --------: |
| app/\_\_init\_\_.py           |        0 |        0 |    100% |           |
| app/config.py                 |       11 |        0 |    100% |           |
| app/exceptions.py             |       14 |        0 |    100% |           |
| app/models/\_\_init\_\_.py    |        0 |        0 |    100% |           |
| app/models/request.py         |       11 |        0 |    100% |           |
| app/models/response.py        |       15 |        0 |    100% |           |
| app/prompts.py                |        6 |        0 |    100% |           |
| app/providers/\_\_init\_\_.py |        0 |        0 |    100% |           |
| app/providers/anthropic.py    |       88 |        4 |     95% |25, 96, 129, 198 |
| app/providers/base.py         |        7 |        1 |     86% |        18 |
| app/providers/factory.py      |        8 |        1 |     88% |        23 |
| app/routers/\_\_init\_\_.py   |        0 |        0 |    100% |           |
| app/routers/ask.py            |       53 |        1 |     98% |        49 |
| app/services/\_\_init\_\_.py  |        0 |        0 |    100% |           |
| app/services/history.py       |       10 |        0 |    100% |           |
| app/services/llm.py           |      108 |        2 |     98% |   118-119 |
| main.py                       |       24 |        1 |     96% |        44 |
| **TOTAL**                     |  **355** |   **10** | **97%** |           |


## Setup coverage badge

Below are examples of the badges you can use in your main branch `README` file.

### Direct image

[![Coverage badge](https://raw.githubusercontent.com/FrostWillmott/Relay/python-coverage-comment-action-data/badge.svg)](https://htmlpreview.github.io/?https://github.com/FrostWillmott/Relay/blob/python-coverage-comment-action-data/htmlcov/index.html)

This is the one to use if your repository is private or if you don't want to customize anything.

### [Shields.io](https://shields.io) Json Endpoint

[![Coverage badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/FrostWillmott/Relay/python-coverage-comment-action-data/endpoint.json)](https://htmlpreview.github.io/?https://github.com/FrostWillmott/Relay/blob/python-coverage-comment-action-data/htmlcov/index.html)

Using this one will allow you to [customize](https://shields.io/endpoint) the look of your badge.
It won't work with private repositories. It won't be refreshed more than once per five minutes.

### [Shields.io](https://shields.io) Dynamic Badge

[![Coverage badge](https://img.shields.io/badge/dynamic/json?color=brightgreen&label=coverage&query=%24.message&url=https%3A%2F%2Fraw.githubusercontent.com%2FFrostWillmott%2FRelay%2Fpython-coverage-comment-action-data%2Fendpoint.json)](https://htmlpreview.github.io/?https://github.com/FrostWillmott/Relay/blob/python-coverage-comment-action-data/htmlcov/index.html)

This one will always be the same color. It won't work for private repos. I'm not even sure why we included it.

## What is that?

This branch is part of the
[python-coverage-comment-action](https://github.com/marketplace/actions/python-coverage-comment)
GitHub Action. All the files in this branch are automatically generated and may be
overwritten at any moment.