plugins {
    java
    application
    id("com.gradleup.shadow") version "9.0.0-beta12"
}

val luceneVersion = "9.12.1"

dependencies {
    implementation(project(":shared"))
    implementation(project(":retriever-api"))
    implementation("org.apache.lucene:lucene-core:$luceneVersion")
    implementation("org.apache.lucene:lucene-analysis-common:$luceneVersion")
    implementation("org.apache.lucene:lucene-queryparser:$luceneVersion")
    implementation("org.apache.lucene:lucene-queries:$luceneVersion")
    implementation("com.fasterxml.jackson.core:jackson-databind:2.18.3")
    implementation("info.picocli:picocli:4.7.6")
    implementation("org.slf4j:slf4j-api:2.0.16")
    runtimeOnly("ch.qos.logback:logback-classic:1.5.16")
}

application {
    mainClass = "com.experiment.retriever.cli.RetrieverCli"
}

tasks.named<com.github.jengelman.gradle.plugins.shadow.tasks.ShadowJar>("shadowJar") {
    archiveBaseName.set("method-retriever")
    archiveClassifier.set("")
    mergeServiceFiles()
    manifest {
        attributes["Main-Class"] = "com.experiment.retriever.cli.RetrieverCli"
        attributes["Multi-Release"] = "true"
    }
    exclude("module-info.class")
    exclude("META-INF/*.SF")
    exclude("META-INF/*.RSA")
    exclude("META-INF/*.DSA")
    exclude("META-INF/INDEX.LIST")
}

tasks.named("jar") {
    enabled = false
}

tasks.named("assemble") {
    dependsOn("shadowJar")
}
