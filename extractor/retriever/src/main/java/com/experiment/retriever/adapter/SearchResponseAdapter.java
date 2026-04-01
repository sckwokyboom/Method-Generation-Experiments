package com.experiment.retriever.adapter;

import com.experiment.retriever.api.IRetrievalResult;
import com.experiment.retriever.api.TypeSourceRetrieveTag;
import com.experiment.retriever.model.SearchResponse;
import com.experiment.retriever.model.SearchResult;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.StringJoiner;

/**
 * Converts {@link IRetrievalResult} lists into {@link SearchResponse} objects
 * that match the JSON format the Python pipeline already parses.
 */
public final class SearchResponseAdapter {

    private SearchResponseAdapter() {}

    /**
     * Converts a list of generic retrieval results into a SearchResponse.
     * If a result is already a {@link LuceneRetrievalResult}, its native data is preserved.
     */
    public static SearchResponse toSearchResponse(
            List<IRetrievalResult> results, long searchTimeMs) {
        List<SearchResult> searchResults = new ArrayList<>(results.size());
        int rank = 0;

        for (IRetrievalResult result : results) {
            rank++;

            if (result instanceof LuceneRetrievalResult luceneResult) {
                // Preserve native Lucene data with updated rank
                SearchResult sr = luceneResult.getDelegate();
                searchResults.add(new SearchResult(
                        sr.id(), sr.filePath(), sr.classFqn(), sr.signature(),
                        sr.methodBody(), sr.methodCard(), sr.invocationProfile(),
                        sr.typeProfile(), sr.score(), rank, sr.explain(),
                        null));
                continue;
            }

            String filePath = result.getLocation() != null ? result.getLocation().toString() : "";
            String explain = formatTagScores(result.getTagScores());

            searchResults.add(new SearchResult(
                    result.getId(),
                    filePath,
                    "", // classFqn not available from IRetrievalResult
                    "", // signature not available
                    "", // methodBody not available
                    "", // methodCard
                    "", // invocationProfile
                    "", // typeProfile
                    result.getScore(),
                    rank,
                    explain,
                    result.getContent()
            ));
        }

        return new SearchResponse(searchResults, results.size(), searchTimeMs, "");
    }

    private static String formatTagScores(Map<TypeSourceRetrieveTag, Float> tagScores) {
        if (tagScores == null || tagScores.isEmpty()) return "";
        StringJoiner sj = new StringJoiner(", ");
        for (Map.Entry<TypeSourceRetrieveTag, Float> entry : tagScores.entrySet()) {
            sj.add(entry.getKey().getName() + "=" + String.format("%.4f", entry.getValue()));
        }
        return sj.toString();
    }
}
