# Politica di sicurezza

## Segnalare una vulnerabilità

Se trovi una vulnerabilità di sicurezza in Visura API, **NON** aprire una issue pubblica.

Invece, segnalala in modo responsabile tramite uno dei seguenti canali:

1. **GitHub Security Advisory**: usa la funzione [Segnala una vulnerabilità](https://github.com/zornade/visura-api/security/advisories/new) direttamente su GitHub
2. **Email**: `sicurezza@zornade.com`, la casella dedicata delle segnalazioni

### Cosa includere nella segnalazione

- Descrizione della vulnerabilità
- Passi per riprodurla
- Possibile impatto
- Eventuali suggerimenti per la correzione

### Tempi di risposta

- **Conferma di ricezione**: entro 48 ore
- **Prima valutazione**: entro 7 giorni
- **Correzione**: dipende dalla gravità, ma ci impegniamo a risolvere le vulnerabilità critiche il prima possibile

## Pratiche di sicurezza del progetto

### Credenziali

- Le credenziali del portale SISTER (ADE_USERNAME, ADE_PASSWORD) **non devono mai** essere committate nel repository
- Usa sempre il file `.env` (incluso nel `.gitignore`) per le credenziali locali
- In produzione, usa variabili d'ambiente o un sistema di gestione dei segreti

### Dati sensibili

Questo progetto interagisce con dati catastali che possono contenere informazioni personali (codici fiscali, nomi, indirizzi). Assicurati di:

- Non includere mai dati reali nei test o nella documentazione
- Rimuovere dati personali dai log prima di condividerli
- Rispettare la normativa sulla protezione dei dati personali (GDPR)

### Dipendenze

- Le dipendenze sono specificate in `requirements.txt` con versioni pinnate
- Aggiorna regolarmente le dipendenze per includere le patch di sicurezza
- Usa `pip audit` per verificare la presenza di vulnerabilità note

## Versioni supportate

| Versione | Supportata |
|---|---|
| Ultima release | Sì, per 5 anni dalla data di rilascio |
| Versioni precedenti | La correzione arriva sull'ultima release, che si aggiorna senza costi |

Il periodo di cinque anni è quello previsto dal Cyber Resilience Act come regola generale. Chi ha un contratto di continuità riceve nell'atto di acquisto la data di fine supporto della versione consegnata, indicata come mese e anno. L'aggiornamento non è mai condizionato al pagamento di un canone. Dettagli su SBOM, Dependabot e segnalazione alle autorità nel documento pubblico [docs/CRA.md](https://github.com/menimenocchio/zornade-app/blob/main/docs/CRA.md).
