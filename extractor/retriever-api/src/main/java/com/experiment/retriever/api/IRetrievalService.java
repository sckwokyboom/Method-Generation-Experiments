package com.experiment.retriever.api;

import java.util.List;

/**
 * Core retrieval service interface. Implementations search for relevant
 * code fragments given a retrieval request.
 *
 * <p>External libraries implement this interface (and its {@link Factory})
 * to provide custom retrieval strategies that integrate with the pipeline.
 */
public interface IRetrievalService {

    String getId();

    List<IRetrievalResult> search(IRetrieveRequest retrieveRequest);

    /**
     * Factory for creating retrieval service instances bound to a specific project.
     *
     * @param <T> the concrete service type produced by this factory
     */
    interface Factory<T extends IRetrievalService> {
        T create(IProject project);
    }
}
