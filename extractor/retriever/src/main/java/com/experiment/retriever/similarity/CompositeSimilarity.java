package com.experiment.retriever.similarity;

import org.apache.lucene.search.similarities.BM25Similarity;
import org.apache.lucene.search.similarities.LMDirichletSimilarity;
import org.apache.lucene.search.similarities.LMJelinekMercerSimilarity;
import org.apache.lucene.search.similarities.MultiSimilarity;
import org.apache.lucene.search.similarities.Similarity;

public final class CompositeSimilarity {
    private CompositeSimilarity() {}

    public static Similarity create() {
        return new MultiSimilarity(new Similarity[]{
                new BM25Similarity(1.2f, 0.75f),
                new LMJelinekMercerSimilarity(0.7f),
                new LMDirichletSimilarity(2000f),
        });
    }
}
