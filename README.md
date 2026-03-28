# homies-messenger
 chat for homies

## Push-уведомления (Firebase Cloud Messaging)

Push-уведомления отправляются офлайн-пользователям при новых сообщениях через Firebase Cloud Messaging (FCM).

### 1. Создание Firebase проекта

1. Перейдите в [Firebase Console](https://console.firebase.google.com/)
2. Нажмите **Add project** и следуйте инструкциям
3. В разделе **Project settings > General** добавьте Android-приложение с package name `com.homies.messenger`

### 2. Получение google-services.json (Android)

1. В Firebase Console: **Project settings > General > Your apps > Android app**
2. Нажмите **Download google-services.json**
3. Скопируйте файл в `android/app/google-services.json`

### 3. Получение firebase-credentials.json (сервер)

1. В Firebase Console: **Project settings > Service accounts**
2. Нажмите **Generate new private key**
3. Сохраните скачанный JSON-файл как `firebase-credentials.json` в корне проекта

### 4. Переменная окружения (альтернатива файлу)

Вместо файла `firebase-credentials.json` можно задать содержимое через переменную окружения:

```bash
export FIREBASE_CREDENTIALS='{"type":"service_account","project_id":"...","private_key":"...","client_email":"...",...}'
```

Или в `docker-compose.yml`:

```yaml
environment:
  - FIREBASE_CREDENTIALS={"type":"service_account",...}
```

### Примечание

Если Firebase credentials не настроены, приложение работает без push-уведомлений (в лог выводится предупреждение).
