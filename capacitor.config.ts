import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.homies.messenger',
  appName: 'Homies',
  webDir: 'static',

  // Для продакшена — укажите URL вашего бэкенда:
  // server: {
  //   url: 'https://YOUR_SERVER_URL',
  //   cleartext: true, // разрешить HTTP (для локальной разработки)
  // },

  plugins: {
    SplashScreen: {
      launchAutoHide: true,
      launchShowDuration: 2000,
      backgroundColor: '#0f0f13',
      showSpinner: false,
    },
    Keyboard: {
      resize: 'body',
      resizeOnFullScreen: true,
    },
    StatusBar: {
      style: 'DARK',
      backgroundColor: '#0f0f13',
    },
  },

  android: {
    allowMixedContent: true, // для локальной разработки с HTTP
  },

  ios: {
    contentInset: 'automatic',
  },
};

export default config;
