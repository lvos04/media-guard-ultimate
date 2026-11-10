pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
        // Spiegel van Maven Central (valt in als Central tijdelijk weigert, bv. HTTP 429)
        maven("https://maven-central.storage-download.googleapis.com/maven2/")
    }
}
rootProject.name = "MediaGuard"
include(":app")
