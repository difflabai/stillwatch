package io.github.difflabai.stillwatch;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import java.net.URI;
import java.security.KeyStore;
import java.util.HashSet;
import java.util.Set;
import java.util.UUID;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.json.JSONObject;

final class Settings {
    private static final String ALIAS="stillwatch-mobile-pairing";
    final SharedPreferences preferences;
    Settings(Context context) { preferences=context.getSharedPreferences("pairing",Context.MODE_PRIVATE); }
    private SecretKey key() throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore"); store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator generator=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            return generator.generateKey();
        }
        return (SecretKey)store.getKey(ALIAS,null);
    }
    void importPairing(JSONObject value) throws Exception {
        if (value.getInt("version")!=1) throw new IllegalArgumentException("Unsupported pairing");
        URI uri=new URI(value.getString("url"));
        if (!"https".equals(uri.getScheme()) || uri.getHost()==null || uri.getUserInfo()!=null || uri.getQuery()!=null || uri.getFragment()!=null)
            throw new IllegalArgumentException("HTTPS endpoint required");
        String device=value.getString("device");
        if (!UUID.fromString(device).toString().equals(device)) throw new IllegalArgumentException("Invalid identity");
        String pin=value.getString("certificate_sha256");
        if (!pin.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("Invalid certificate pin");
        String token=value.getString("token");
        if (token.length()<16 || token.length()>256) throw new IllegalArgumentException("Invalid token");
        if (ready() && !device.equals(device())) throw new IllegalArgumentException("Reset app data before pairing a different phone identity");
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE,key());
        byte[] encrypted=cipher.doFinal(token.getBytes("UTF-8"));
        preferences.edit().putString("url",uri.toString()).putString("device",device).putString("label",value.getString("label"))
            .putString("pin",pin).putString("cipher",Base64.encodeToString(encrypted,Base64.NO_WRAP))
            .putString("iv",Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP)).apply();
    }
    String token() throws Exception {
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(preferences.getString("iv",""),Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(preferences.getString("cipher",""),Base64.NO_WRAP)),"UTF-8");
    }
    boolean ready() { return preferences.contains("device") && preferences.contains("cipher"); }
    String device() { return preferences.getString("device",""); }
    String label() { return preferences.getString("label","Phone"); }
    String url() { return preferences.getString("url",""); }
    String pin() { return preferences.getString("pin",""); }
    long cursor() { return preferences.getLong("cursor",0); }
    boolean cloudAllowed(String pkg) { return preferences.getStringSet("cloud_apps",new HashSet<String>()).contains(pkg); }
    Set<String> cloudApps() { return new HashSet<>(preferences.getStringSet("cloud_apps",new HashSet<String>())); }
    void cloudApps(Set<String> values) { preferences.edit().putStringSet("cloud_apps",values).apply(); }
    void succeeded(long cursor) { preferences.edit().putLong("cursor",cursor).putLong("last_sync",System.currentTimeMillis()).putString("error","").apply(); }
    void failed(Exception error) { preferences.edit().putString("error",error.getClass().getSimpleName()).apply(); }
}
