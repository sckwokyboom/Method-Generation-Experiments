plugins {
    java
    application
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

repositories {
    mavenCentral()
}

dependencies {
    implementation("org.eclipse.jdt:org.eclipse.jdt.core:3.39.0")
    implementation("com.fasterxml.jackson.core:jackson-databind:2.18.3")
    implementation("info.picocli:picocli:4.7.6")
    implementation("org.slf4j:slf4j-api:2.0.16")
    runtimeOnly("ch.qos.logback:logback-classic:1.5.16")
}

application {
    mainClass = "com.experiment.extractor.cli.ExtractorCli"
}

tasks.jar {
    archiveBaseName = "method-extractor"
    manifest {
        attributes["Main-Class"] = "com.experiment.extractor.cli.ExtractorCli"
    }

    duplicatesStrategy = DuplicatesStrategy.EXCLUDE

    from(configurations.runtimeClasspath.get().map { if (it.isDirectory) it else zipTree(it) })

    // Exclude module-info: would turn the fat JAR into a named module, breaking class loading.
    exclude("module-info.class")
    exclude("META-INF/versions/*/module-info.class")

    // Exclude JAR signing metadata: Eclipse JDT ships as a signed JAR; including its
    // .SF/.RSA/.DSA files in a fat JAR causes the JVM to reject the archive as tampered.
    exclude("META-INF/*.SF")
    exclude("META-INF/*.RSA")
    exclude("META-INF/*.DSA")
    exclude("META-INF/INDEX.LIST")
}
