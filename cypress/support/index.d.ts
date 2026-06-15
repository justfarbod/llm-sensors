// load the global Cypress types
/// <reference types="cypress" />

declare namespace Cypress {
	interface Chainable {
		login(email: string, password: string): Chainable<Element>;
		register(name: string, email: string, password: string): Chainable<Element>;
		registerAdmin(): Chainable<Element>;
		loginAdmin(): Chainable<Element>;
		registerNormalUser(): Chainable<Element>;
		loginNormalUser(): Chainable<Element>;
		registerExperimentUser(): Chainable<Element>;
		loginExperimentUser(): Chainable<Element>;
		uploadTestDocument(suffix: any): Chainable<Element>;
		deleteTestDocument(suffix: any): Chainable<Element>;
	}
}
