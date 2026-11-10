import groovy.json.JsonSlurper

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
    alias(libs.plugins.paparazzi)
}

// Versie = MediaGuard-versie uit deploy/release-manifest.json (één bron voor alles).
val manifestFile = rootProject.file("../../deploy/release-manifest.json")
@Suppress("UNCHECKED_CAST")
val mgVersion: String = if (manifestFile.exists()) {
    ((JsonSlurper().parse(manifestFile) as Map<String, Any?>)["version"] as String?) ?: "0.0.0"
} else "0.0.0"
// Bouwnummer binnen de versie (0–99, CI: scripts/android-build-number.sh = commits sinds de
// versiewijziging). versionCode = versie*100 + bouwnummer: een handmatige of tussentijdse build is
// zo altijd een update (Android, Obtainium), en de volgende versie ligt altijd hoger.
val mgBuild: Int = (System.getenv("MG_ANDROID_BUILD")?.trim()?.toIntOrNull() ?: 0).coerceIn(0, 99)
val mgVersionCode: Int = mgVersion.split(".").map { it.toIntOrNull() ?: 0 }
    .let { (it.getOrElse(0) { 0 } * 10000) + (it.getOrElse(1) { 0 } * 100) + it.getOrElse(2) { 0 } }
    .let { it * 100 + mgBuild }
    .coerceAtLeast(1)
// Release-builds heten gewoon "1.2.1"; andere builds "1.2.1+3" (de app vergelijkt zonder "+…").
val mgVersionName: String =
    if (mgBuild > 0 && System.getenv("MG_ANDROID_RELEASE") != "1") "$mgVersion+$mgBuild" else mgVersion

android {
    namespace = "nl.mediaguard.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "nl.mediaguard.app"
        minSdk = 26
        targetSdk = 35
        versionCode = mgVersionCode
        versionName = mgVersionName
    }

    signingConfigs {
        // Release-ondertekening via omgevingsvariabelen (GitHub-secrets); zonder sleutel valt de
        // release-build terug op de debug-sleutel, zodat er altijd een installeerbare APK is.
        create("release") {
            val ks = System.getenv("MG_ANDROID_KEYSTORE")
            if (!ks.isNullOrBlank() && file(ks).exists()) {
                storeFile = file(ks)
                storePassword = System.getenv("MG_ANDROID_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("MG_ANDROID_KEY_ALIAS")
                keyPassword = System.getenv("MG_ANDROID_KEY_PASSWORD")
            } else {
                // Vaste, bewust NIET-geheime sideload-sleutel (op verzoek van de eigenaar): elke build
                // heeft dezelfde handtekening, zodat updates via GitHub/Obtainium zonder opnieuw
                // installeren werken. Wie een eigen sleutel wil, zet de GitHub-secrets (docs/ANDROID.md).
                storeFile = rootProject.file("signing/mediaguard-sideload.keystore")
                storePassword = "mediaguard-sideload"
                keyAlias = "mediaguard"
                keyPassword = "mediaguard-sideload"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            val rel = signingConfigs.getByName("release")
            // MG_ANDROID_UNSIGNED=1: niet-ondertekende release-build (app-release-unsigned.apk), alleen om lokaal of in
            // een test te controleren dat de release zonder sleutel bouwt. Zonder die variabele verandert er niets.
            signingConfig = when {
                System.getenv("MG_ANDROID_UNSIGNED") == "1" -> null
                rel.storeFile != null -> rel
                else -> signingConfigs.getByName("debug")
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true }
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
}

// Unit- en schermafdruktests in UTC: tijden in de afdrukken ("Laatst bekend · …") zijn dan lokaal en op de
// CI-runner gelijk, zodat :app:verifyPaparazziDebug niet van de tijdzone van de machine afhangt.
tasks.withType<Test>().configureEach {
    systemProperty("user.timezone", "UTC")
    // PEIL_AFDRUKKEN=1 maakt ook de afdrukken voor docs/screenshots/peil/android/ (zie shots/ScreenShots.kt)
    inputs.property("peilAfdrukken", System.getenv("PEIL_AFDRUKKEN") ?: "")
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    implementation(libs.compose.material3)
    implementation(libs.retrofit)
    implementation(libs.retrofit.serialization)
    implementation(libs.okhttp)
    implementation(libs.kotlinx.serialization.json)
    implementation(libs.coil.compose)
    implementation(libs.work.runtime)
    implementation(libs.security.crypto)
    implementation(libs.zxing.embedded)

    testImplementation(libs.junit)
    testImplementation(libs.okhttp.mockwebserver)
    testImplementation(libs.kotlinx.coroutines.test)
}
