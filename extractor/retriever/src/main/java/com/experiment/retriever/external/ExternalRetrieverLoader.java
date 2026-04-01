package com.experiment.retriever.external;

import com.experiment.retriever.api.IProject;
import com.experiment.retriever.api.IRetrievalService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.URL;
import java.net.URLClassLoader;
import java.nio.file.Path;
import java.util.List;

/**
 * Dynamically loads external {@link IRetrievalService} implementations from JAR files.
 *
 * <p>Uses a child-first {@link URLClassLoader} with the retriever-api classes
 * as the parent classloader, ensuring the API interfaces are shared while
 * external implementation classes are isolated.
 */
public final class ExternalRetrieverLoader {

    private static final Logger log = LoggerFactory.getLogger(ExternalRetrieverLoader.class);

    private ExternalRetrieverLoader() {}

    /**
     * Loads an external retrieval service from the given JARs.
     *
     * @param factoryClassName fully qualified name of the {@link IRetrievalService.Factory} implementation
     * @param jarPaths         paths to JAR files containing the implementation
     * @param project          the project to pass to the factory
     * @return a configured retrieval service instance
     * @throws ReflectiveOperationException if the factory class cannot be loaded or instantiated
     */
    public static IRetrievalService load(
            String factoryClassName, List<Path> jarPaths, IProject project)
            throws ReflectiveOperationException {

        URL[] urls = new URL[jarPaths.size()];
        for (int i = 0; i < jarPaths.size(); i++) {
            try {
                urls[i] = jarPaths.get(i).toUri().toURL();
            } catch (Exception e) {
                throw new IllegalArgumentException("Invalid JAR path: " + jarPaths.get(i), e);
            }
        }

        log.info("Loading external retriever factory: {} from {} JAR(s)",
                factoryClassName, jarPaths.size());

        URLClassLoader classLoader = new URLClassLoader(
                urls, IRetrievalService.class.getClassLoader());

        Class<?> factoryClass = classLoader.loadClass(factoryClassName);

        if (!IRetrievalService.Factory.class.isAssignableFrom(factoryClass)) {
            throw new IllegalArgumentException(
                    factoryClassName + " does not implement IRetrievalService.Factory");
        }

        @SuppressWarnings("unchecked")
        IRetrievalService.Factory<? extends IRetrievalService> factory =
                (IRetrievalService.Factory<? extends IRetrievalService>)
                        factoryClass.getDeclaredConstructor().newInstance();

        IRetrievalService service = factory.create(project);
        log.info("External retriever loaded: id={}", service.getId());
        return service;
    }
}
