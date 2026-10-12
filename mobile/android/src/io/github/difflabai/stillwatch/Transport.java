package io.github.difflabai.stillwatch;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.URL;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.cert.CertificateException;
import java.security.cert.X509Certificate;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;
import org.json.JSONObject;

final class Transport {
    static byte[] hex(String text) {
        byte[] bytes=new byte[text.length()/2];
        for(int i=0;i<bytes.length;i++) bytes[i]=(byte)Integer.parseInt(text.substring(i*2,i*2+2),16);
        return bytes;
    }
    static boolean pinned(X509Certificate certificate,byte[] pin) throws Exception {
        certificate.checkValidity();
        return MessageDigest.isEqual(MessageDigest.getInstance("SHA-256").digest(certificate.getEncoded()),pin);
    }
    static JSONObject exchange(Settings settings,JSONObject request) throws Exception {
        final byte[] pin=hex(settings.pin());
        TrustManager[] trust=new TrustManager[]{new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
            public void checkClientTrusted(X509Certificate[] chain,String auth) throws CertificateException {throw new CertificateException("Client certificates not used");}
            public void checkServerTrusted(X509Certificate[] chain,String auth) throws CertificateException {
                try {
                    if(chain==null || chain.length==0 || !pinned(chain[0],pin)) throw new CertificateException("Pairing certificate mismatch");
                } catch(CertificateException error){throw error;}
                catch(Exception error){throw new CertificateException("Invalid pinned certificate",error);}
            }
        }};
        SSLContext tls=SSLContext.getInstance("TLS");tls.init(null,trust,new SecureRandom());
        URL url=new URL(settings.url()+"/v1/phone/exchange");
        if(!"https".equals(url.getProtocol())) throw new IllegalArgumentException("HTTPS required");
        HttpsURLConnection connection=(HttpsURLConnection)url.openConnection();
        connection.setSSLSocketFactory(tls.getSocketFactory());
        // Exact leaf-certificate pairing is the endpoint identity, including private IPs.
        // Never accept another certificate or disable validation globally.
        connection.setHostnameVerifier((host,session)->{
            try{return pinned((X509Certificate)session.getPeerCertificates()[0],pin);}catch(Exception e){return false;}
        });
        connection.setInstanceFollowRedirects(false);
        connection.setConnectTimeout(8000);connection.setReadTimeout(12000);
        connection.setRequestMethod("POST");connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type","application/json");
        connection.setRequestProperty("Authorization","Bearer "+settings.token());
        byte[] data=request.toString().getBytes("UTF-8");
        connection.setFixedLengthStreamingMode(data.length);
        try {
            try(java.io.OutputStream out=connection.getOutputStream()){out.write(data);}
            if(connection.getResponseCode()!=200) throw new java.io.IOException("Bridge refused request");
            ByteArrayOutputStream output=new ByteArrayOutputStream();
            try(InputStream input=connection.getInputStream()) {
                byte[] buffer=new byte[8192];int read;
                while((read=input.read(buffer))!=-1){output.write(buffer,0,read);if(output.size()>2000000)throw new java.io.IOException("Oversized response");}
            }
            return new JSONObject(output.toString("UTF-8"));
        } finally {connection.disconnect();}
    }
}
