# Phone and Pixel Watch bridge

The Android companion captures notifications in its Android user profile, queues them privately, and syncs them to Stillwatch's authoritative Linux server. That server remains the source of truth for deduplication, classification and handling. Important shared events from any paired device can return as Android notifications, which a paired Pixel Watch can mirror.

**Status: mobile alpha, not yet validated on a real phone or watch.** The APK compiles and is signature-verified. Automated tests exercise HTTPS authentication, consent, retries, authority checks and bidirectional dismissal. They do not verify Android lifecycle behavior, GrapheneOS setup, battery use or watch delivery. Desktop attention modals do not become full-screen watch modals; the companion uses standard Android notifications without an expiry timeout.

## Server setup

Install Stillwatch and designate its authoritative server as described in the main README. The mobile API belongs on that server, alongside the classifier, separate from SSH desktop replication.

```sh
stillwatch phone setup                 # HTTPS on loopback only, port 8794
stillwatch phone status
```

This creates a private, self-signed TLS certificate and enables `stillwatch-phone.service` under the user manager. Its certificate and private key stay in `~/.config/stillwatch/phone-tls/`. No public listener, relay, DNS name or certificate-transparency registration is created. Stop it with `systemctl --user disable --now stillwatch-phone.service`.

For an initial USB check, forward the server's loopback port through the laptop using an existing trusted SSH connection, then reverse it through an authorized phone's ADB connection:

```sh
ssh -N -L 127.0.0.1:8794:127.0.0.1:8794 SERVER
# In another terminal, with the phone connected and USB debugging authorized:
adb reverse tcp:8794 tcp:8794
```

On the server, generate a private pairing file:

```sh
stillwatch phone pair --url https://127.0.0.1:8794 --label Phone --output ~/.config/stillwatch/phone-pairing.json
```

Transfer this file privately to the phone. It contains a bearer credential and the exact server certificate fingerprint. Never commit, publish or share it. Import it through the companion's file picker, then remove transfer copies. The server stores only a token hash; Android encrypts its token using an Android Keystore key and disables app backup. Certificate validation is pinned to the imported certificate, including private IP addresses. Rotating the certificate requires new pairing. To revoke a phone, stop the bridge, remove its entry from private `phone-clients.json`, then restart it.

USB forwarding lasts only while connected. For normal operation, use a phone-reachable private network or VPN address, bind explicitly with `stillwatch phone setup --bind PRIVATE_SERVER_IP`, and pair using that address. This alpha has no push relay and does not make a home server reachable from cellular networks automatically. Do not forward this development API onto the public Internet.

## Phone setup

Install the signed APK from the release assets, or build your own using [the Android source README](../mobile/android/README.md). Android 9+ is required.

1. Import your private pairing file in Stillwatch.
2. Select apps under **Choose apps for AI**. The allowlist initially contains no apps. New content from selected apps can go to NanoGPT through your server; other apps stay in private shared history without cloud classification. Changing the list does not retroactively classify old notifications.
3. Enable **notification access** for Stillwatch in Android Settings. This broad permission lets the listener read and dismiss notifications in that profile. If Android blocks access for a sideloaded app, review that app's Settings access controls before granting it.
4. Allow Stillwatch to post notifications. Review the amber attention and red severe channels' sound, vibration and lock-screen settings.
5. Review battery settings for Stillwatch. The listener polls about every five seconds while Android keeps it running, and captures new posts when connected. There is no Google push service, wake lock or guaranteed background delivery. Doze, network loss and process suspension can delay synchronization. Validate screen-off behavior.

Original phone notifications must stay enabled for capture. Set their channels to Silent if you want Stillwatch to control phone interruptions. A listener observes notifications after Android posts them; it cannot atomically prevent the original phone alert. Calls, alarms and system full-screen interfaces have separate behavior.

## Put Stillwatch before the watch

Pair the watch using its supported official companion. [Google's Pixel Watch setup](https://support.google.com/googlepixelwatch/answer/12651780) describes its requirements. Stillwatch itself needs no Google Play. If using sandboxed Google Play on GrapheneOS, install it in the same profile as apps that need it; see [GrapheneOS instructions](https://grapheneos.org/usage#sandboxed-google-play).

In the Pixel Watch app's phone-app notification settings:

- Enable **Stillwatch** mirroring.
- Disable mirroring for original apps whose phone notifications you want Stillwatch to filter.
- Check apps installed directly on the watch separately. Watch-local or standalone cellular events are outside the phone listener's reach.

Timely `immediate` server decisions generate Stillwatch notifications. Ordinary attention is amber; only severe decisions use red. Routine, excluded, failed and delayed decisions remain in the computers' shared history. Server cooldown and deduplication still apply. The phone forwards server decisions without its own model key or independent classifier.

**Mark handled** or an Android swipe-dismiss queues a shared acknowledgement. Handling on a computer cancels matching companion alerts and the latest matching original phone notification when Android marks it clearable. Automatic expiry, replacement and app shutdown do not count as handling. Watch-to-phone dismissal depends on the actual watch bridge and remains to be verified. Opening an alert opens Stillwatch; it does not approve an agent action, run a command or send a reply.

## Real-device acceptance check

Keep existing watch routing until these checks pass:

1. A fictional routine phone event reaches shared history without a Stillwatch alert.
2. An opted-in attention request produces one amber alert on phone, watch and computers. Test a fictional severe case separately.
3. Handling on the laptop clears the phone/watch alert. Repeat by dismissing on the phone, then on the watch.
4. A new event after five minutes can alert; duplicate deliveries and repeated updates during cooldown do not spam.
5. Network disconnection/reconnection catches up events and dismissals without restoring handled alerts.
6. Repeat while the phone is locked and asleep long enough for battery-saving behavior. Check cellular/VPN reachability if that is the intended route.

## Capture limits

Android can redact authentication codes for untrusted notification listeners; see [Android 15 notification protection](https://developer.android.com/about/versions/15/behavior-changes-all). Listener access is scoped to profiles and subject to device policy; see [NotificationListenerService](https://developer.android.com/reference/android/service/notification/NotificationListenerService). There is no root or accessibility bypass. Protected text, other profiles, watch-native events and alarms cannot be promised as universal capture.

The mobile queue contains private text and can grow during prolonged disconnection. Uninstalling removes its local queue and pairing state. Shared server/computer copies follow Stillwatch's retention policy. This alpha should not be the sole channel for emergencies.
