#!/usr/bin/env python3
"""Build a small native Android APK with installed SDK tools; no Gradle or GMS."""
import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--sdk',required=True,help='SDK root containing android.jar and build-tools')
parser.add_argument('--java',default='/usr/lib/jvm/java-21-openjdk-amd64')
parser.add_argument('--compiler',help='Optional Eclipse ECJ jar when only a JRE is installed')
parser.add_argument('--sign',action='store_true',help='Use/create a private release key outside the checkout')
args=parser.parse_args()
sdk=Path(args.sdk).expanduser()
platforms=[p for p in sdk.rglob('android.jar') if p.parent.name=='android-36']
preferred=sdk/'build-tools/36.0.0/aapt2'
tools=[preferred] if preferred.exists() else list((sdk/'android-16').glob('aapt2'))
if not platforms or not tools:raise SystemExit('Install Android SDK platform 36 and build-tools 36 first.')
android=platforms[0];tool=tools[0].parent
build=ROOT/'build/android'
if build.exists():shutil.rmtree(build)
for name in ('classes','generated','dex'):(build/name).mkdir(parents=True)
env={**os.environ,'JAVA_HOME':args.java}
def run(argv):subprocess.run([str(v) for v in argv],env=env,check=True)
run([tool/'aapt2','compile','--dir',ROOT/'mobile/android/res','-o',build/'resources.zip'])
run([tool/'aapt2','link','-I',android,'--manifest',ROOT/'mobile/android/AndroidManifest.xml','--java',build/'generated','--version-code','4','--version-name','0.4.0-alpha','-o',build/'base.apk','-R',build/'resources.zip'])
sources=list((ROOT/'mobile/android/src').rglob('*.java'))+list((build/'generated').rglob('*.java'))
compiler = [Path(args.java)/'bin/java','-jar',Path(args.compiler).expanduser()] if args.compiler else [Path(args.java)/'bin/javac']
compiler += ['-source','8','-target','8','-g:source,lines','-classpath',android,'-d',build/'classes',*sources]
run(compiler)
run([tool/'d8','--lib',android,'--min-api','28','--output',build/'dex',*list((build/'classes').rglob('*.class'))])
with zipfile.ZipFile(build/'base.apk','a') as apk:
    for dex in (build/'dex').glob('*.dex'):apk.write(dex,dex.name)
output=ROOT/'dist';output.mkdir(exist_ok=True)
aligned=build/'aligned.apk'
run([tool/'zipalign','-p','4',build/'base.apk',aligned])
if args.sign:
    private=Path(os.environ.get('XDG_CONFIG_HOME',str(Path.home()/'.config')))/'stillwatch/mobile-signing'
    private.mkdir(parents=True,exist_ok=True,mode=0o700);private.chmod(0o700)
    password_file=private/'password'
    if not password_file.exists():password_file.write_text(secrets.token_urlsafe(32));password_file.chmod(0o600)
    env['STILLWATCH_SIGNING_PASSWORD']=password_file.read_text()
    keystore=private/'release.p12'
    if not keystore.exists():
        run([Path(args.java)/'bin/keytool','-genkeypair','-keystore',keystore,'-storetype','PKCS12','-storepass:env','STILLWATCH_SIGNING_PASSWORD','-keypass:env','STILLWATCH_SIGNING_PASSWORD','-alias','stillwatch','-keyalg','RSA','-keysize','3072','-validity','3650','-dname','CN=Stillwatch,O=difflabai'])
        keystore.chmod(0o600)
    target=output/'stillwatch-android-0.4.0-alpha.apk'
    run([tool/'apksigner','sign','--ks',keystore,'--ks-key-alias','stillwatch','--ks-pass','env:STILLWATCH_SIGNING_PASSWORD','--key-pass','env:STILLWATCH_SIGNING_PASSWORD','--out',target,aligned])
    run([tool/'apksigner','verify',target])
else:
    target=output/'stillwatch-android-0.4.0-unsigned.apk';shutil.copyfile(aligned,target)
print('Built',target.name,'; pairing credentials are not embedded.')
