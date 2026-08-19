export const API_BASE = '/admin/api';

// Per-page caches (fresh on each page load)
let allDatasetItems = [];
let allQuestions = [];
let allSynonyms = [];

export function getDatasetItems() { return allDatasetItems; }
export function setDatasetItems(items) { allDatasetItems = items; }

export function getQuestions() { return allQuestions; }
export function setQuestions(items) { allQuestions = items; }

export function getSynonyms() { return allSynonyms; }
export function setSynonyms(items) { allSynonyms = items; }
