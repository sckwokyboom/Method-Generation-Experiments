package com.experiment.retriever.search;

import com.experiment.retriever.index.FieldConstants;
import com.experiment.retriever.model.SearchRequest;
import com.experiment.retriever.model.SearchResponse;
import com.experiment.retriever.model.SearchResult;
import com.experiment.retriever.similarity.CompositeSimilarity;
import org.apache.lucene.document.Document;
import org.apache.lucene.index.DirectoryReader;
import org.apache.lucene.search.Explanation;
import org.apache.lucene.search.IndexSearcher;
import org.apache.lucene.search.Query;
import org.apache.lucene.search.ScoreDoc;
import org.apache.lucene.search.TopDocs;
import org.apache.lucene.store.FSDirectory;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public class SearchExecutor implements AutoCloseable {
    private static final Logger log = LoggerFactory.getLogger(SearchExecutor.class);

    private final DirectoryReader reader;
    private final IndexSearcher searcher;

    public SearchExecutor(Path indexDir) throws IOException {
        this.reader = DirectoryReader.open(FSDirectory.open(indexDir));
        this.searcher = new IndexSearcher(reader);
        this.searcher.setSimilarity(CompositeSimilarity.create());
    }

    public SearchResponse search(SearchRequest request, String targetMethodBody) {
        long startMs = System.currentTimeMillis();
        int topK = request.topK() > 0 ? request.topK() : 5;

        Query query = QueryBuilder.build(request);
        String queryDebug = query.toString();

        try {
            int fetchCount = topK * 3 + 10;
            TopDocs topDocs = searcher.search(query, fetchCount);

            LeakageFilter filter = new LeakageFilter(request);
            List<SearchResult> results = new ArrayList<>();
            Map<String, Integer> classCount = new HashMap<>();
            int maxPerClass = 2;
            int rank = 0;

            for (ScoreDoc scoreDoc : topDocs.scoreDocs) {
                if (results.size() >= topK) break;

                Document doc = searcher.doc(scoreDoc.doc);
                String filePath = doc.get(FieldConstants.FILE_PATH);
                String classFqn = doc.get(FieldConstants.CLASS_FQN);
                int bodyStartOffset = doc.getField(FieldConstants.BODY_START_OFFSET) != null
                        ? doc.getField(FieldConstants.BODY_START_OFFSET).numericValue().intValue()
                        : -1;
                String methodBody = doc.get(FieldConstants.METHOD_BODY);

                if (filter.shouldExclude(filePath, classFqn, bodyStartOffset)) {
                    continue;
                }

                int count = classCount.getOrDefault(classFqn, 0);
                if (count >= maxPerClass) {
                    continue;
                }
                classCount.put(classFqn, count + 1);

                // Lucene explain for score decomposition
                String explainStr = summarizeExplanation(query, scoreDoc.doc);

                rank++;
                results.add(new SearchResult(
                        doc.get(FieldConstants.ID),
                        filePath,
                        classFqn,
                        doc.get(FieldConstants.SIGNATURE),
                        methodBody,
                        doc.get(FieldConstants.METHOD_CARD),
                        doc.get(FieldConstants.INVOCATION_PROFILE),
                        doc.get(FieldConstants.TYPE_PROFILE),
                        scoreDoc.score,
                        rank,
                        explainStr,
                        null
                ));
            }

            long elapsed = System.currentTimeMillis() - startMs;
            @SuppressWarnings("deprecation")
            int totalHitCount = Math.toIntExact(topDocs.totalHits.value);
            return new SearchResponse(results, totalHitCount, elapsed, queryDebug);

        } catch (IOException e) {
            log.error("Search failed", e);
            long elapsed = System.currentTimeMillis() - startMs;
            return new SearchResponse(List.of(), 0, elapsed, queryDebug + " ERROR: " + e.getMessage());
        }
    }

    private String summarizeExplanation(Query query, int docId) {
        try {
            Explanation explanation = searcher.explain(query, docId);
            StringBuilder sb = new StringBuilder(512);
            formatExplanation(sb, explanation, 0);
            return sb.toString();
        } catch (IOException e) {
            return "explain error: " + e.getMessage();
        }
    }

    private void formatExplanation(StringBuilder sb, Explanation exp, int depth) {
        // Only show first 2 levels to keep it manageable
        if (depth > 2) return;
        String indent = "  ".repeat(depth);
        sb.append(indent)
          .append(String.format("%.2f", exp.getValue().floatValue()))
          .append(" ")
          .append(exp.getDescription())
          .append("\n");
        for (Explanation sub : exp.getDetails()) {
            formatExplanation(sb, sub, depth + 1);
        }
    }

    @Override
    public void close() throws IOException {
        reader.close();
    }
}
