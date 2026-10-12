# Stillwatch Android companion

Native Java app with no Gradle, AndroidX, telemetry or Google Play dependencies. It uses a notification listener, private SQLite queue, Android Keystore credentials and certificate-pinned HTTPS. See [setup and limitations](../../docs/mobile.md).

Install Android SDK platform 36 and build-tools 36.0.0 plus a JDK (17+), then run from the repository root:

```sh
python3 scripts/build_android.py --sdk "$ANDROID_HOME" --java "$JAVA_HOME"
```

This builds an unsigned artifact. Add `--sign` for an installable APK. The builder creates or reuses a private signing key under `~/.config/stillwatch/mobile-signing/`, outside the checkout. Keep it for future updates and back it up privately; never publish it. The public signing certificate uses only Stillwatch/difflabai attribution. No pairing credential or model key is embedded. An optional `--compiler /path/to/ecj.jar` supports Eclipse ECJ when only a JRE is available.

CI builds unsigned without credentials. A successful build does not establish phone/watch compatibility; this alpha requires the acceptance checks in the setup guide.
