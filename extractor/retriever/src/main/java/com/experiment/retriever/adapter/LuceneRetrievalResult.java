package com.experiment.retriever.adapter;

import com.experiment.retriever.api.IRetrievalResult;
import com.experiment.retriever.api.TypeSourceRetrieveTag;
import com.experiment.retriever.model.SearchResult;

import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Wraps a Lucene {@link SearchResult} as an {@link IRetrievalResult}.
 * Tag scores expose the overall Lucene score under a "lucene_total" tag.
 */
public class LuceneRetrievalResult implements IRetrievalResult {

    private static final TypeSourceRetrieveTag TAG_TOTAL =
            new TypeSourceRetrieveTag("lucene_total", "Lucene Total Score");

    private final SearchResult delegate;
    private final Map<TypeSourceRetrieveTag, Float> tagScores;

    public LuceneRetrievalResult(SearchResult delegate) {
        this.delegate = delegate;
        this.tagScores = new LinkedHashMap<>();
        this.tagScores.put(TAG_TOTAL, delegate.score());
    }

    @Override
    public String getId() {
        return delegate.id();
    }

    @Override
    public Path getLocation() {
        return Path.of(delegate.filePath());
    }

    @Override
    public float getScore() {
        return delegate.score();
    }

    @Override
    public Map<TypeSourceRetrieveTag, Float> getTagScores() {
        return tagScores;
    }

    @Override
    public String getContent() {
        return delegate.methodBody();
    }

    public SearchResult getDelegate() {
        return delegate;
    }
}
