// 自動當沖 · 夥伴看盤 App(2026-11-15 使用者「看盤版 改成App 變成apk可以下載安裝」)
// 和名揚四海的 android/ 完全分開的專案(不共用程式、不改 android/ 任何檔);只借用同一把簽章(android/keystore/mysh.jks)
pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS); repositories { google(); mavenCentral() } }
rootProject.name = "PartnerViewer"
include(":app")
