---
name: maven-gradle
description: Maven or Gradle builds and tests.
teammates: [backend_engineer, frontend_engineer, generalist_engineer, devops_engineer]
---
# Maven and Gradle

- Use the wrapper the project ships (`./mvnw`, `./gradlew`), never a system install with another
  version.
- Maven: `./mvnw -q test`, one test `-Dtest=ClassName#method`; dependencies in `pom.xml`
  `<dependencies>`, versions often from a parent or BOM - change them there.
- Gradle: `./gradlew test`, one test `--tests 'pkg.ClassName.method'`; dependencies in
  `build.gradle(.kts)`, often through a version catalogue (`gradle/libs.versions.toml`).
- Test reports: `target/surefire-reports/` (Maven), `build/reports/tests/` (Gradle).
- JUnit 5 uses `org.junit.jupiter.api`; do not mix it with JUnit 4 annotations in one class.
