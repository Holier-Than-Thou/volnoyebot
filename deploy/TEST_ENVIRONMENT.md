# Тестовая среда на VDS

Тестовый контур изолирован от рабочего:

- код: `/opt/volnoyebot-test`, ветка `feature/fishing`;
- окружение: `/etc/volnoyebot-test/volnoyebot.env`;
- данные: `/var/lib/volnoyebot-test`;
- сервис: `volnoyebot-test.service`;
- Web App: `https://nikitaabashevst.fvds.ru/test/fishing.html`;
- статическая сборка: `/var/www/volnoyebot-test`.

## Обновление тестовой версии

```bash
sudo -u volnoyebot-test git -C /opt/volnoyebot-test pull --ff-only origin feature/fishing
sudo -u volnoyebot-test /opt/volnoyebot-test/.venv/bin/pip install -r /opt/volnoyebot-test/requirements.txt
sudo -u volnoyebot-test npm --prefix /opt/volnoyebot-test/webapp ci
sudo -u volnoyebot-test env VITE_BASE_PATH=/test/ npm --prefix /opt/volnoyebot-test/webapp run build
rsync -a --delete /opt/volnoyebot-test/webapp/dist/ /var/www/volnoyebot-test/
systemctl restart volnoyebot-test
```

Перед изменением схемы тестовой БД её резервная копия создаётся отдельно от
рабочих бэкапов. Рабочий сервис `volnoyebot` этими командами не затрагивается.
