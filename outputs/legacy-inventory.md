# Реестр старого сайта и полнота миграции

Сформировано: 2026-07-12T12:03:52+00:00

## Состояние обследования

- обследовано URL: **1615**;
- осталось в очереди предыдущего прохода: **0**;
- успешных ответов: **1606**;
- ответов 4xx/5xx: **9**;
- подробных снимков с текстом и медиа: **1615** (совпали с crawl: **1611**);
- полный crawl: **да**.

## Типы материалов

| Тип старого материала | Найдено | Новая сущность |
|---|---:|---|
| `announcement` | 5 | `news` |
| `broken` | 9 | `redirect/review` |
| `clergy` | 7 | `clergy` |
| `contacts` | 1 | `site_contact` |
| `gallery_album` | 622 | `gallery` |
| `home` | 1 | `page` |
| `leaflet_index` | 1 | `page` |
| `news` | 668 | `news` |
| `online_request` | 1 | `page/form` |
| `page` | 2 | `page` |
| `parish_life` | 187 | `parish_section/news/gallery` |
| `saint` | 5 | `page` |
| `schedule` | 1 | `service/page` |
| `school` | 100 | `parish_section/page/gallery` |
| `shrine` | 4 | `page` |
| `stream` | 1 | `video/page` |

## Нерабочие или подозрительные URL

| HTTP | Путь | Причина проверки |
|---:|---|---|
| 404 | `/voskresnaya-shkola/zhizn/рождественский-праздник-состоялся-❗️12-января-2025-г.-воскресенье❗️.html` | ответ старого сайта |
| 404 | `/536.html` | ответ старого сайта |
| 404 | `/zhizn-prihoda/semeynyy-klub-karavay.html` | ответ старого сайта |
| 404 | `/zhivoe-slovo/publikacii/cerkov-dolzhna-zashhishhat-zhizn.-kommentariy-protoiereya-mihaila-dudko-po-povodu-zayavleniya-odnogo-iz-angliyskih-religioznyh-deyateley-o-vozmozhnosti-evtanazii-dlya-tyazhelobolnyh-ludey.html` | ответ старого сайта |
| 404 | `/zhizn-prihoda/izostudiya-obraz.html` | ответ старого сайта |
| 404 | `/zhizn-prihoda/molodezhnyy-klub/molodezhnyy-klub-sovershil-vyezdnuu-ekskursiu-russkoe-selo1.html` | ответ старого сайта |
| 404 | `/o-hrame/anonsy/vedetsya-nabor-detey-i-vzroslyh-v-shahmatnyy-klub.html` | ответ старого сайта |
| 404 | `/zhizn-prihoda/semeynyy-klub-trezvosti-voshozhdenie.html` | ответ старого сайта |
| 404 | `/zhivoe-slovo/publikacii/zdes-budet-hram.-beseda-s-nastoyatelem-hrama-prav.-aleksiya-mecheva-iereem-nikolaem-fateevym.html` | ответ старого сайта |

Подробные снимки вне списка crawl:
- `/o-hrame/fotogalereya.html`

## Вывод

Текущий реестр доказывает структуру ключевых разделов, но не является полным экспортом архива. До переключения домена нужно завершить очередь, получить подробный снимок каждой успешной страницы, зеркалировать медиа и сверить 301-редиректы.
