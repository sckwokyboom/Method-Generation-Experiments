plugins {
    java
    application
}

dependencies {
    implementation(project(":shared"))
    implementation("org.eclipse.jdt:org.eclipse.jdt.core:3.39.0")
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

    dependsOn(configurations.runtimeClasspath)
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE

    from({ configurations.runtimeClasspath.get().map { if (it.isDirectory) it else zipTree(it) } })

    exclude("module-info.class")
    exclude("META-INF/versions/*/module-info.class")
    exclude("META-INF/*.SF")
    exclude("META-INF/*.RSA")
    exclude("META-INF/*.DSA")
    exclude("META-INF/INDEX.LIST")
}
