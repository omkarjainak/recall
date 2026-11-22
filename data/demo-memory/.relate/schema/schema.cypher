CREATE VECTOR INDEX `fact_embedding` FOR (n:`Fact`) ON (n.`embedding`) WITH [n.`closedAt`] OPTIONS {indexConfig: {`vector.default_search_expansion_factor`: 3.0,`vector.dimensions`: 256,`vector.hnsw.ef_construction`: 100,`vector.hnsw.m`: 16,`vector.quantization.type`: 'BINARY',`vector.similarity_function`: 'COSINE'}};
CREATE RANGE INDEX `fact_recorded` FOR (n:`Fact`) ON (n.`recordedAt`);
CREATE RANGE INDEX `fact_slot` FOR (n:`Fact`) ON (n.`slot`);
CREATE RANGE INDEX `fact_status` FOR (n:`Fact`) ON (n.`status`);
CREATE FULLTEXT INDEX `fact_text` FOR (n:`Fact`) ON EACH [n.`text`] OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words',`fulltext.eventually_consistent`: false}};
CREATE CONSTRAINT `entity_key` FOR (n:`Entity`) REQUIRE (n.`key`) IS UNIQUE;
CREATE CONSTRAINT `episode_id` FOR (n:`Episode`) REQUIRE (n.`id`) IS UNIQUE;
CREATE CONSTRAINT `fact_id` FOR (n:`Fact`) REQUIRE (n.`id`) IS UNIQUE;
