# Politica di sicurezza

## Segnalare una vulnerabilità

Se trovi una vulnerabilità di sicurezza in Visura API, **NON** aprire una issue pubblica.

Invece, segnalala in modo responsabile tramite uno dei seguenti canali:

1. **GitHub Security Advisory**: usa la funzione [Segnala una vulnerabilità](https://github.com/zornade/visura-api/security/advisories/new) direttamente su GitHub
2. **Email**: contatta i manutentori tramite il profilo GitHub

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
|----------|-----------|
| Ultima   | Sì        |
| Vecchie  | No        |

Solo l'ultima versione del progetto riceve aggiornamenti di sicurezza.
