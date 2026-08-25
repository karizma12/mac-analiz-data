# AI Maç Analiz — Daily Mirror

Bu repo GitHub Actions üzerinde saat başı FotMob public daily matches verisini çeker.
İş bilgisayarı FotMob'a bağlanmaz; yalnız raw.githubusercontent.com'daki `data/today.json`
dosyasını indirir.

Kurulum:
1. Bu klasörün içeriğini PUBLIC bir GitHub repository'ye yükle.
2. Actions sekmesinden workflow'u bir kez elle çalıştır.
3. `data/today.json` oluşunca RAW adresini kopyala.
4. AI Maç Analiz içinde MIRROR AYARI'na bas ve RAW adresini yapıştır.

Örnek RAW:
https://raw.githubusercontent.com/KULLANICI/REPO/main/data/today.json
