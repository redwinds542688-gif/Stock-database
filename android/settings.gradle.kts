pluginManagement { repositories { maven { url = uri("file:///tmp/claude-0/-home-user-Stock-database/63200f43-e3d1-5183-9eb6-04fa1ce41864/scratchpad/m2repo") }; google(); mavenCentral(); gradlePluginPortal() } }
dependencyResolutionManagement { repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS); repositories { maven { url = uri("file:///tmp/claude-0/-home-user-Stock-database/63200f43-e3d1-5183-9eb6-04fa1ce41864/scratchpad/m2repo") }; google(); mavenCentral() } }
rootProject.name = "MyshMonitor"
include(":app")
