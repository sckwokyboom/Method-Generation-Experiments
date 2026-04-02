package com.experiment.retriever.adapter;

import com.experiment.retriever.api.*;
import com.experiment.retriever.model.SearchRequest;
import com.experiment.retriever.model.SearchResponse;
import com.experiment.retriever.model.SearchResult;
import com.experiment.retriever.search.SearchExecutor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.IOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Adapts the existing Lucene-based {@link SearchExecutor} to the
 * {@link IRetrievalService} interface.
 *
 * <p>The Lucene retriever requires a pre-built index and uses rich
 * {@link SearchRequest} objects internally. When receiving the simpler
 * {@link IRetrieveRequest}, it builds a minimal SearchRequest from
 * the available source code and offset information.
 */
public class LuceneRetrievalService implements IRetrievalService, AutoCloseable {

    private static final Logger log = LoggerFactory.getLogger(LuceneRetrievalService.class);

    private final SearchExecutor executor;
    private final Path indexDir;
    private final int topK;

    public LuceneRetrievalService(SearchExecutor executor, Path indexDir, int topK) {
        this.executor = executor;
        this.indexDir = indexDir;
        this.topK = topK;
    }

    @Override
    public String getId() {
        return "lucene";
    }

    @Override
    public List<IRetrievalResult> search(IRetrieveRequest retrieveRequest) {
        log.debug("Adapter search: location={}, offset={}, topK={}",
                retrieveRequest.getLocation(), retrieveRequest.getOffset(), topK);

        SearchRequest searchRequest = toSearchRequest(retrieveRequest);
        String targetBody = extractTargetBody(retrieveRequest);
        SearchResponse response = executor.search(searchRequest, targetBody);

        List<IRetrievalResult> results = new ArrayList<>(response.results().size());
        for (SearchResult sr : response.results()) {
            results.add(new LuceneRetrievalResult(sr));
        }

        if (log.isDebugEnabled()) {
            log.debug("Adapter returned {} results", results.size());
            int limit = Math.min(results.size(), 3);
            for (int i = 0; i < limit; i++) {
                IRetrievalResult r = results.get(i);
                log.debug("  top-{}: id={}, score={}", i + 1, r.getId(), String.format("%.4f", r.getScore()));
            }
        }

        return results;
    }

    /**
     * Performs a full Lucene search using the native {@link SearchRequest} format,
     * returning the raw {@link SearchResponse}. Use this when the caller has
     * the full request metadata (as built by the Python pipeline).
     */
    public SearchResponse searchNative(SearchRequest request, String targetMethodBody) {
        return executor.search(request, targetMethodBody);
    }

    @Override
    public void close() throws IOException {
        executor.close();
    }

    private SearchRequest toSearchRequest(IRetrieveRequest request) {
        return new SearchRequest(
                "", // methodSignature — not available from IRetrieveRequest
                "", // classFqn
                List.of(), // supertypes
                List.of(), // imports
                List.of(), // classFields
                List.of(), // siblingSignatures
                List.of(), // siblingOwnerTypes
                List.of(), // siblingUsedTypes
                extractPrefix(request), // fimPrefix
                extractSuffix(request), // fimSuffix
                request.getLocation() != null ? request.getLocation().toString() : "",
                request.getOffset(),
                topK,
                0.8 // nearDuplicateThreshold
        );
    }

    private String extractPrefix(IRetrieveRequest request) {
        String source = request.getSourceCode();
        int offset = request.getOffset();
        if (source == null || offset <= 0) return "";
        int start = Math.max(0, offset - 500);
        return source.substring(start, Math.min(offset, source.length()));
    }

    private String extractSuffix(IRetrieveRequest request) {
        String source = request.getSourceCode();
        int offset = request.getOffset();
        if (source == null || offset < 0) return "";
        if (request instanceof RetrieveRequestWithMaskingVariant masked) {
            MaskingRange range = masked.getMaskingVariant().getMaskingRange();
            int suffixStart = range.maskingEndOffset();
            int end = Math.min(suffixStart + 500, source.length());
            return source.substring(Math.min(suffixStart, source.length()), end);
        }
        int end = Math.min(offset + 500, source.length());
        return source.substring(Math.min(offset, source.length()), end);
    }

    private String extractTargetBody(IRetrieveRequest request) {
        if (request instanceof RetrieveRequestWithMaskingVariant masked) {
            MaskingRange range = masked.getMaskingVariant().getMaskingRange();
            String source = request.getSourceCode();
            if (source != null && range.maskingEndOffset() <= source.length()) {
                return source.substring(range.maskingStartOffset(), range.maskingEndOffset());
            }
        }
        return "";
    }

    /**
     * Factory that creates a {@link LuceneRetrievalService} for the given project.
     * The Lucene index directory must be set via the system property
     * {@code retriever.lucene.indexDir} or the environment variable
     * {@code RETRIEVER_LUCENE_INDEX_DIR}.
     */
    public static class Factory implements IRetrievalService.Factory<LuceneRetrievalService> {

        @Override
        public LuceneRetrievalService create(IProject project) {
            String indexDirStr = System.getProperty("retriever.lucene.indexDir",
                    System.getenv().getOrDefault("RETRIEVER_LUCENE_INDEX_DIR", ""));
            if (indexDirStr.isEmpty()) {
                throw new IllegalStateException(
                        "Lucene index directory not set. Use -Dretriever.lucene.indexDir=<path> " +
                        "or set RETRIEVER_LUCENE_INDEX_DIR environment variable.");
            }
            int topK = Integer.parseInt(System.getProperty("retriever.lucene.topK", "5"));
            Path indexDir = Path.of(indexDirStr);
            try {
                SearchExecutor executor = new SearchExecutor(indexDir);
                return new LuceneRetrievalService(executor, indexDir, topK);
            } catch (IOException e) {
                throw new RuntimeException("Failed to open Lucene index at " + indexDir, e);
            }
        }
    }
}
