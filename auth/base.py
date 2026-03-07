from abc import ABC, abstractmethod

from playwright.async_api import Page


class BaseAuthProvider(ABC):
    """Abstract base class for authentication providers."""

    @abstractmethod
    async def login(self, page: Page, username: str, password: str, page_logger=None) -> None:
        """
        Executes the login flow.

        Args:
            page (Page): The playwright page to execute on.
            username (str): The ADE username.
            password (str): The ADE password.

        Raises:
            Exception: If login fails or times out.
        """
        pass
