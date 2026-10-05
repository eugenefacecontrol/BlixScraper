# Обычный ChatGPT: подтверждённое подключение

Проверено по официальной документации OpenAI 05.10.2026. Настройка MCP в Codex сама по себе не подключает инструмент к обычному ChatGPT.

[ChatGPT Developer mode](https://developers.openai.com/api/docs/guides/developer-mode) описывает доступ на **web** для Plus, Pro, Business, Enterprise и Education. Settings → Security and login → Developer mode; затем [ChatGPT Plugins](https://chatgpt.com/apps), создание developer-mode app и выбор приложения через меню Plus в чате. UI и разрешения могут зависеть от аккаунта/администратора. Документация подтверждает SSE и streaming HTTP. Прямой запуск локального stdio через обычный ChatGPT Desktop в этом режиме не подтверждён. Используйте ChatGPT Web как подтверждённую точку подключения; Desktop нельзя считать проверенным до отдельного теста в вашем аккаунте.

## Приватный локальный сервер: Secure MCP Tunnel

[Официальная инструкция Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) подтверждает путь к локальному HTTP или stdio без публичного слушателя и входящих портов. Требуется `tunnel_id`, отдельный runtime API key для tunnel-client и разрешения Platform Tunnels. Ключ нужен транспорту; сам BlixScraper не использует платные вызовы модели и не требует OpenAI/Gemini API для расчётов.

1. В [Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels) создайте туннель, связанный с нужным ChatGPT workspace/личной организацией; требуются Read + Manage для создания, Read + Use для запуска и выбора.
2. Получите официальный `tunnel-client` через ссылку на загрузку в этих настройках или [последний официальный релиз](https://github.com/openai/tunnel-client/releases/latest). Не устанавливайте глобально: бинарник можно оставить в отдельной локальной папке.
3. Запустите `uv run blix serve --transport streamable-http` в папке проекта.
4. По quickstart текущей версии клиента настройте HTTP-профиль с `--mcp-server-url http://127.0.0.1:8765/mcp`, вашим `--tunnel-id` и ключом транспорта в `CONTROL_PLANE_API_KEY`. Команда `tunnel-client help quickstart` показывает актуальную схему; ключ не помещайте в Git, чат или shell history.
5. Выполните `tunnel-client doctor --profile PROFILE --explain`, затем `tunnel-client run --profile PROFILE`.
6. При создании приложения ChatGPT выберите Connection → Tunnel и нужный туннель; если он отсутствует, проверьте связь workspace и разрешения. Добавьте приложение в обычный чат в Developer mode и вставьте [русские инструкции](assistant-instructions.ru.md).

Туннель в этой работе не создан и не запущен: аккаунт, права и секреты не предоставлены. Публичный сервер не опубликован. Необходимый выбор перед подключением: приватный Secure MCP Tunnel с отдельным транспортным ключом или публичный HTTPS с OAuth.

## Альтернатива без OpenAI API-ключа: защищённый HTTPS

[Официальный MCP quickstart](https://developers.openai.com/plugins/build/app-quickstart) описывает публичный HTTPS-туннель к локальному `/mcp`. Для постоянного использования нужен стабильный адрес и OAuth-сервис/шлюз перед этим сервером: ChatGPT поддерживает OAuth, No Authentication и Mixed Authentication, но произвольный статический Bearer-пароль не следует считать поддержанным UI способом.

В этом проекте HTTP-сервер без собственной OAuth-аутентификации и слушает только loopback. **Не публикуйте его напрямую.** Для публичного варианта сначала выберите OAuth-провайдер/шлюз и HTTPS-домен; шлюз должен проверять доступ ко всем MCP-запросам, а backend оставаться локальным. Такой шлюз здесь не развёрнут и не заявлен как готовый. Без разрешения пользователя внешний доступ не настраивается.

## Проверка в вашем аккаунте

После подключения вызовите `data_status`, `search_offers` с `query: "mleko"`, `get_offer` для найденного ID и `compare_basket` с примером корзины. Проверьте, что ChatGPT сообщает неполный охват, не принимает JSON-цену за подтверждённую стоимость и не считает акции по отсутствующим картам. Локальные протокольные проверки не заменяют этот тест.
