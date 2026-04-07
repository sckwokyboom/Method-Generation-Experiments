package com.experiment.retriever.index;

import com.experiment.retriever.similarity.CompositeSimilarity;
import com.experiment.shared.model.ExtractedMethod;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.lucene.document.Document;
import org.apache.lucene.document.Field;
import org.apache.lucene.document.IntPoint;
import org.apache.lucene.document.StoredField;
import org.apache.lucene.document.StringField;
import org.apache.lucene.document.TextField;
import org.apache.lucene.index.IndexWriter;
import org.apache.lucene.index.IndexWriterConfig;
import org.apache.lucene.analysis.standard.StandardAnalyzer;
import org.apache.lucene.store.FSDirectory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

public class IndexBuilder {
    private static final Logger log = LoggerFactory.getLogger(IndexBuilder.class);
    private static final ObjectMapper MAPPER = new ObjectMapper();

    public void buildIndex(Path inputPath, Path indexDir) throws IOException {
        log.info("Reading extraction data from {}", inputPath);

        if (Files.exists(indexDir)) {
            log.info("Removing existing index at {}", indexDir);
            deleteRecursive(indexDir);
        }
        Files.createDirectories(indexDir);

        IndexWriterConfig config = new IndexWriterConfig(new StandardAnalyzer());
        config.setSimilarity(CompositeSimilarity.create());
        config.setOpenMode(IndexWriterConfig.OpenMode.CREATE);

        try (FSDirectory dir = FSDirectory.open(indexDir);
             IndexWriter writer = new IndexWriter(dir, config);
             JsonParser parser = MAPPER.getFactory().createParser(inputPath.toFile())) {

            // Navigate to the "methods" array by scanning top-level fields
            advanceToMethodsArray(parser);

            int indexed = 0;
            while (parser.nextToken() != JsonToken.END_ARRAY) {
                ExtractedMethod method = MAPPER.readValue(parser, ExtractedMethod.class);
                Document doc = createDocument(method);
                writer.addDocument(doc);
                indexed++;
                if (indexed % 5000 == 0) {
                    log.info("Indexed {}/? methods ...", indexed);
                }
            }

            writer.commit();
            log.info("Indexed {} methods to {}", indexed, indexDir);
        }
    }

    /**
     * Advance the parser to the start of the "methods" JSON array.
     * Skips all other top-level fields (projectName, meta, classpath, etc.).
     */
    private void advanceToMethodsArray(JsonParser parser) throws IOException {
        if (parser.nextToken() != JsonToken.START_OBJECT) {
            throw new IOException("Expected JSON to start with '{'");
        }
        while (parser.nextToken() != JsonToken.END_OBJECT) {
            String fieldName = parser.currentName();
            parser.nextToken(); // move to the value
            if ("methods".equals(fieldName)) {
                if (parser.currentToken() != JsonToken.START_ARRAY) {
                    throw new IOException("Expected 'methods' to be a JSON array");
                }
                return; // positioned at START_ARRAY, caller will iterate
            }
            // Skip non-methods fields entirely (projectName, meta, classpath)
            parser.skipChildren();
        }
        throw new IOException("JSON does not contain a 'methods' field");
    }

    private Document createDocument(ExtractedMethod method) throws IOException {
        Document doc = new Document();

        String id = method.classFqn() + "#" + method.methodName() + "#" + method.bodyStartOffset();
        doc.add(new StringField(FieldConstants.ID, id, Field.Store.YES));
        doc.add(new StringField(FieldConstants.FILE_PATH, method.filePath(), Field.Store.YES));
        doc.add(new StringField(FieldConstants.CLASS_FQN, method.classFqn(), Field.Store.YES));

        String methodCard = MethodCardBuilder.build(method);
        doc.add(new TextField(FieldConstants.METHOD_CARD, methodCard, Field.Store.YES));

        String body = method.methodBody();
        if (body != null && body.length() <= 5000) {
            doc.add(new TextField(FieldConstants.METHOD_BODY, body, Field.Store.YES));
        } else {
            doc.add(new TextField(FieldConstants.METHOD_BODY, "", Field.Store.YES));
        }

        String invProfile = InvocationProfileBuilder.build(method.invocations());
        doc.add(new TextField(FieldConstants.INVOCATION_PROFILE, invProfile, Field.Store.YES));

        String typeProfile = TypeProfileBuilder.build(method);
        doc.add(new TextField(FieldConstants.TYPE_PROFILE, typeProfile, Field.Store.YES));

        doc.add(new StoredField(FieldConstants.SIGNATURE, method.methodSignature()));
        doc.add(new IntPoint(FieldConstants.BODY_START_OFFSET, method.bodyStartOffset()));
        doc.add(new StoredField(FieldConstants.BODY_START_OFFSET, method.bodyStartOffset()));

        String rawJson = MAPPER.writeValueAsString(method);
        doc.add(new StoredField(FieldConstants.RAW_JSON, rawJson));

        return doc;
    }

    private void deleteRecursive(Path path) throws IOException {
        if (Files.isDirectory(path)) {
            try (var entries = Files.list(path)) {
                for (Path entry : entries.toList()) {
                    deleteRecursive(entry);
                }
            }
        }
        Files.deleteIfExists(path);
    }
}
